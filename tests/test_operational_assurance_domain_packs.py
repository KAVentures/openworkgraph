"""Independent real retained-log acceptance for the operational assurance suite."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from server.event_log_domain_packs_operational_assurance import OPERATIONAL_ASSURANCE_PACKS


# Independent role sequence and deliberate error witnesses. Not generated from
# the production definitions, so role omissions/order changes cannot mask errors.
CASES = {
    "access_recertification": (
        "cert_case", "opened assigned evidence queried decided applied verified closed",
        "assign_hours", "opened", "assigned", "decided", "applied", "verified",
        "queried", ("applied", "verified"), "recert_opened_to_assigned"),
    "vulnerability_remediation": (
        "remediation_case", "registered assessed assigned deferred treated retested reviewed closed",
        "assess_hours", "registered", "assessed", "registered", "treated", "retested",
        "deferred", ("treated", "retested"), "vuln_registered_to_assessed"),
    "inventory_discrepancy_reconciliation": (
        "discrepancy_case", "detected assigned recounted queried reconciled approved posted verified closed",
        "assign_hours", "detected", "assigned", "detected", "reconciled", "approved",
        "queried", ("reconciled", "approved"), "invrec_detected_to_assigned"),
    "continuity_exercise_review": (
        "exercise_case", "planned approved prepared conducted findings reworked corrected verified closed",
        "approval_hours", "planned", "approved", "conducted", "corrected", "verified",
        "reworked", ("corrected", "verified"), "bcex_planned_to_approved"),
}
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def fixture(key):
    case, role_text, param, a, b, duplicate, prior, following, optional, pair, metric = CASES[key]
    roles = role_text.split()
    names = {role: "System: " + role + ", recorded" for role in roles}
    normal = list(zip(roles, range(len(roles))))
    rows = []

    def add(cid, events, missing_resources=False):
        for role, hour in events:
            actor = "" if missing_resources else "actor_" + role
            if cid == "same actor" and role in pair:
                actor = "same reviewer"
            rows.append([cid, names[role], (START + timedelta(hours=hour)).isoformat(), actor])

    add("ordinary", normal)
    add("late", [(role, 4000 if role == b else hour) for role, hour in normal])
    add("missing response", [(role, hour) for role, hour in normal if role != b])
    add("duplicate milestone", normal + [(duplicate, 12)])
    add("wrong order", [(role, 11 if role == prior else hour) for role, hour in normal])
    add("same actor", normal)
    add("unknown actor", normal, missing_resources=True)
    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(["case", "activity", "time", "resource"])
    writer.writerows(rows)
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
        and r["rule"]["a"] == "System: " + a + ", recorded"
        and r["rule"].get("b") == (None if b is None else "System: " + b + ", recorded")
    )


def witnesses(row):
    return {entry["case_id"] for entry in row["violating_case_examples"]}


@pytest.mark.parametrize("key", CASES)
def test_retained_log_pipeline_and_explicit_deviations(key):
    _, _, _, a, b, duplicate, prior, following, _, pair, metric = CASES[key]
    log, spec = fixture(key)
    installed = packs.install(log, key, spec)
    assert len(installed["metric_ids"]) == 7
    assert installed["not_applicable"] == []
    report = packs.report(log, key)
    assert report["pack"]["definitions_sha256"] == installed["definitions_sha256"]
    assert report["not_applicable"] == []
    assert not report["source_system_validated"]
    assert not report["policy_compliance_verified"]
    assert not report["execution_authorized"]
    deadline = rule(report, "response_within", a, b)
    assert {"late", "missing response"} <= witnesses(deadline)
    assert "ordinary" not in witnesses(deadline)
    assert "duplicate milestone" in witnesses(rule(report, "existence", duplicate))
    assert "wrong order" in witnesses(rule(report, "precedence", prior, following))
    sod = rule(report, "segregation_of_duties", *pair)
    assert "same actor" in witnesses(sod)
    assert sod["unknown_cases"] >= 1
    assert "ordinary" not in witnesses(sod)
    value = next(row for row in report["metrics"] if row["name"] == metric)
    assert value["median"] == 3600
    assert value["coverage"]["cases_with_value"] == 6
    assert report["object_model"]["summary"]["objects"] == 7
    assert 0 < report["object_model"]["summary"]["fitting_objects"] < 7


@pytest.mark.parametrize("key", CASES)
def test_parameter_revision_optional_mapping_and_uninstall(key):
    _, _, param, a, b, _, _, _, optional, _, _ = CASES[key]
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
                                       "activity": "System: " + a + ", recorded"}})
    packs.uninstall(log, key)
    assert [row["metric"]["name"] for row in query.list_metrics(log)["metrics"]] == ["manual_count"]


@pytest.mark.parametrize("key", CASES)
def test_invalid_install_preserves_existing_setup(key):
    _, roles, param, *_ = CASES[key]
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


def test_catalog_and_global_metric_namespace():
    assert set(CASES) == set(OPERATIONAL_ASSURANCE_PACKS)
    catalog = {p["pack_id"]: p for p in packs.catalog()["packs"]}
    owners = {}
    for versions in packs.PACKS.values():
        for definition in versions.values():
            for metric in definition["metrics"]:
                name = metric["metric"]["name"]
                owner = definition["pack_id"]
                assert name not in owners or owners[name] == owner
                owners[name] = owner
    for key, case in CASES.items():
        definition = packs.definitions(key)
        assert catalog[key]["latest_version"] == "1.0.0"
        assert [r["role"] for r in definition["activities"]] == case[1].split()
        assert len(definition["metrics"]) == 7
        assert len(definition["parameters"]) == 4
        assert 12 <= len(definition["compliance"]) <= 20
        assert definition["object_rules"] == []
        definition["metrics"].clear()
        assert len(packs.definitions(key)["metrics"]) == 7
