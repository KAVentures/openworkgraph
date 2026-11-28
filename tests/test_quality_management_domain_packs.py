"""Exercise the existing retained-log installation and reporting path for R23 quality packs."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from server.event_log_domain_packs_quality_management import QUALITY_MANAGEMENT_PACKS

# Expected cases and roles are independent of the installed definitions.
CASES = {
    "corrective_action_management": ("action_case", "opened investigated queried planned approved implemented verified closed", "queried", "investigate_hours", "opened", "investigated", "approved", ("implemented", "verified"), "capa_opened_to_investigated"),
    "calibration_administration": ("calibration_case", "requested scheduled performed exception certificate reviewed closed", "exception", "schedule_hours", "requested", "scheduled", "requested", ("performed", "reviewed"), "calq_requested_to_scheduled"),
    "controlled_document_revision": ("revision_case", "requested drafted returned reviewed approved published acknowledged closed", "returned", "draft_hours", "requested", "drafted", "published", ("drafted", "approved"), "docctl_requested_to_drafted"),
    "product_recall_notification_admin": ("notification_case", "opened scoped drafted approved dispatched exception reconciled closed", "exception", "scope_hours", "opened", "scoped", "approved", ("dispatched", "reconciled"), "recall_opened_to_scoped"),
}
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def fixture(key):
    case, text, optional, _, a, b, duplicate, pair, _ = CASES[key]
    roles = text.split()
    names = {role: "Recorded: " + role + ", quality" for role in roles}
    normal = list(zip(roles, range(len(roles))))
    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(["case", "activity", "time", "resource"])

    def add(cid, events, missing_resources=False):
        for role, hour in events:
            actor = "" if missing_resources else "actor_" + role
            if cid == "same actor" and role in pair:
                actor = "same reviewer"
            writer.writerow([cid, names[role], (START + timedelta(hours=hour)).isoformat(), actor])

    add("ordinary", normal)
    add("optional absent", [(r, h) for r, h in normal if r != optional])
    add("late", [(r, 4000 if r == b else h) for r, h in normal])
    add("missing response", [(r, h) for r, h in normal if r != b])
    add("duplicate milestone", normal + [(duplicate, 12)])
    add("wrong order", [(r, 11 if r == a else h) for r, h in normal])
    add("same actor", normal)
    add("unknown actor", normal, missing_resources=True)
    log = workspace.import_log(
        BytesIO(stream.getvalue().encode()), name=key, source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity",
                 "timestamp_column": "time", "resource_column": "resource"},
    )["log_id"]
    return log, {"version": "1.0.0", "mapping": {
        "object_types": {case: "case"}, "activities": names}}


def rule(report, template, a, b=None):
    return next(
        r for group in report["compliance"] for r in group["results"]
        if r["rule"]["template"] == template
        and r["rule"]["a"] == "Recorded: " + a + ", quality"
        and r["rule"].get("b") == (None if b is None else "Recorded: " + b + ", quality")
    )


def witnesses(row):
    return {entry["case_id"] for entry in row["violating_case_examples"]}


@pytest.mark.parametrize("key", CASES)
def test_retained_log_install_and_review(key):
    _, _, optional, _, a, b, duplicate, pair, metric = CASES[key]
    log, spec = fixture(key)
    installed = packs.install(log, key, spec)
    assert len(installed["metric_ids"]) == 7
    report = packs.report(log, key)
    assert report["pack"]["definitions_sha256"] == installed["definitions_sha256"]
    assert report["not_applicable"] == []
    assert not report["source_system_validated"]
    assert not report["policy_compliance_verified"]
    assert not report["execution_authorized"]
    deadline = rule(report, "response_within", a, b)
    assert {"late", "missing response"} <= witnesses(deadline)
    assert "ordinary" not in witnesses(deadline)
    assert "optional absent" not in witnesses(deadline)
    assert "duplicate milestone" in witnesses(rule(report, "existence", duplicate))
    assert "wrong order" in witnesses(rule(report, "precedence", a, b))
    sod = rule(report, "segregation_of_duties", *pair)
    assert "same actor" in witnesses(sod)
    assert sod["unknown_cases"] >= 1
    assert "ordinary" not in witnesses(sod)
    value = next(row for row in report["metrics"] if row["name"] == metric)
    assert value["median"] == 3600
    assert report["object_model"]["summary"]["objects"] == 8
    assert 0 < report["object_model"]["summary"]["fitting_objects"] < 8


@pytest.mark.parametrize("key", CASES)
def test_reconfigure_and_remove_only_owned_metrics(key):
    _, _, optional, param, a, b, _, _, _ = CASES[key]
    log, spec = fixture(key)
    original = packs.install(log, key, spec)
    revised = deepcopy(spec)
    revised["parameters"] = {param: .25}
    installed = packs.install(log, key, revised)
    assert original["definitions_sha256"] != installed["definitions_sha256"]
    strict = rule(packs.report(log, key), "response_within", a, b)
    assert strict["rule"]["seconds"] == 900
    assert "ordinary" in witnesses(strict)
    revised["mapping"]["activities"].pop(optional)
    installed = packs.install(log, key, revised)
    assert optional in {r["unmapped_role"] for r in installed["not_applicable"]}
    assert packs.report(log, key)["not_applicable"] == installed["not_applicable"]
    query.save_metric(log, {"metric": {"name": "manual_count", "kind": "activity_count",
                                       "activity": "Recorded: " + a + ", quality"}})
    packs.uninstall(log, key)
    assert [row["metric"]["name"] for row in query.list_metrics(log)["metrics"]] == ["manual_count"]


@pytest.mark.parametrize("key", CASES)
def test_invalid_mapping_preserves_prior_installation(key):
    _, roles, _, param, *_ = CASES[key]
    log, spec = fixture(key)
    first_role = roles.split()[0]
    installed = packs.install(log, key, spec)
    for mutate in (
        lambda s: s["mapping"]["activities"].pop(first_role),
        lambda s: s["mapping"]["activities"].update({first_role: "Not retained"}),
        lambda s: s.update(parameters={param: -1}),
    ):
        invalid = deepcopy(spec)
        mutate(invalid)
        with pytest.raises(workspace.EventLogWorkspaceError):
            packs.install(log, key, invalid)
        assert packs.report(log, key)["pack"]["definitions_sha256"] == installed["definitions_sha256"]
        assert len(query.list_metrics(log)["metrics"]) == 7


def test_catalog_registration_and_global_metric_namespace():
    catalog = {p["pack_id"]: p for p in packs.catalog()["packs"]}
    owners = {}
    for versions in packs.PACKS.values():
        for definition in versions.values():
            for metric in definition["metrics"]:
                name = metric["metric"]["name"]
                owner = definition["pack_id"]
                assert name not in owners or owners[name] == owner
                owners[name] = owner
    assert set(QUALITY_MANAGEMENT_PACKS) == set(CASES)
    for key, case in CASES.items():
        assert catalog[key]["latest_version"] == "1.0.0"
        definition = packs.definitions(key)
        assert [r["role"] for r in definition["activities"]] == case[1].split()
        assert len(definition["metrics"]) == 7
        definition["metrics"].clear()
        assert len(packs.definitions(key)["metrics"]) == 7
