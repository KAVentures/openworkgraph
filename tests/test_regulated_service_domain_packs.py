"""Independent retained-log acceptance for the regulated service suite."""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from server.event_log_domain_packs_regulated_services import REGULATED_SERVICE_PACKS


# Independent role order, primary clock, duplicate and inversion witnesses.
# No fixtures are generated from the production reference model.
CASES = {
    "healthcare_referral_administration": (
        "referral_case", "received checked routed returned scheduled notified closed",
        "check_hours", "received", "checked", "closed", "scheduled", "notified",
        "returned", None, "refadmin_received_to_checked"),
    "public_permit_administration": (
        "permit_case", "filed screened supplement assessed reviewed decided notified archived",
        "screen_hours", "filed", "screened", "decided", "decided", "notified",
        "supplement", ("assessed", "reviewed"), "permitadm_filed_to_screened"),
    "construction_rfi_administration": (
        "rfi_case", "submitted assigned clarification drafted reviewed issued acknowledged closed",
        "assign_hours", "submitted", "assigned", "closed", "drafted", "issued",
        "clarification", ("drafted", "reviewed"), "rfiadm_submitted_to_assigned"),
    "customs_documentation_administration": (
        "declaration_case", "opened documents checked filed query response archived",
        "documents_hours", "opened", "documents", "archived", "filed", "response",
        "query", ("documents", "checked"), "customsadm_opened_to_documents"),
    "education_admissions_administration": (
        "application_case", "submitted checked query assessed reviewed decided communicated closed",
        "check_hours", "submitted", "checked", "decided", "decided", "communicated",
        "query", ("assessed", "reviewed"), "admitadm_submitted_to_checked"),
    "research_grant_administration": (
        "grant_case", "submitted reviewed awarded setup report reconciled closed",
        "review_hours", "submitted", "reviewed", "awarded", "setup", "report",
        "reconciled", ("report", "reconciled"), "grantadm_submitted_to_reviewed"),
}
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def fixture(key):
    case, role_text, param, a, b, duplicate, prior, following, optional, pair, metric = CASES[key]
    roles = role_text.split()
    # Exact literal source labels; neither labels nor case identifiers are role IDs.
    names = {role: "System: " + role + ", recorded" for role in roles}
    normal = list(zip(roles, range(len(roles))))
    rows = []

    def add(cid, events, missing_resources=False):
        for role, hour in events:
            actor = "" if missing_resources else "actor_" + role
            if pair and cid == "same actor" and role in pair:
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
    definition = packs.definitions(key)
    assert key in {p["pack_id"] for p in packs.catalog()["packs"]}
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
    if pair:
        sod = rule(report, "segregation_of_duties", *pair)
        assert "same actor" in witnesses(sod)
        assert sod["unknown_cases"] >= 1
        assert "ordinary" not in witnesses(sod)
    value = next(row for row in report["metrics"] if row["name"] == metric)
    assert value["median"] == 3600
    assert value["coverage"]["cases_with_value"] == 6
    assert report["object_model"]["summary"]["objects"] == 7
    assert report["object_model"]["summary"]["fitting_objects"] < 7
    assert len(definition["compliance"]) >= 12


@pytest.mark.parametrize("key", CASES)
def test_parameter_revision_and_all_optional_omissions(key):
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
    # Optional roles can be dropped, including an optional primary clock.
    omitted = {role["role"] for role in packs.definitions(key)["activities"] if not role["required"]}
    assert optional in omitted
    for role in omitted:
        revised["mapping"]["activities"].pop(role)
    installed = packs.install(log, key, revised)
    assert omitted <= {r["unmapped_role"] for r in installed["not_applicable"]}
    report = packs.report(log, key)
    assert omitted <= {r["unmapped_role"] for r in report["not_applicable"]}
    assert len(query.list_metrics(log)["metrics"]) == len(installed["metric_ids"])
    packs.uninstall(log, key)
    assert query.list_metrics(log)["metrics"] == []


@pytest.mark.parametrize("key", CASES)
def test_invalid_install_is_atomic_and_does_not_remove_existing_setup(key):
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


def test_catalog_is_complete_and_metric_names_are_unique():
    assert set(CASES) == set(REGULATED_SERVICE_PACKS)
    owners = {}
    for versions in packs.PACKS.values():
        for definition in versions.values():
            for metric in definition["metrics"]:
                name = metric["metric"]["name"]
                owner = definition["pack_id"]
                assert name not in owners or owners[name] == owner
                owners[name] = owner
    for key in CASES:
        definition = packs.definitions(key)
        clone = packs.definitions(key)
        assert definition == clone and definition is not clone
        clone["activities"].clear()
        assert packs.definitions(key)["activities"]
