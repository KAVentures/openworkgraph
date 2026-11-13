"""Insurance claims operations pack: synthetic event logs only (R23).

No real claimant records, policy contents, or verified decision/settlement claims.
"""
from __future__ import annotations

from io import BytesIO
import copy

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace

PACK = "insurance_claims"
MAPPING = {
    "object_types": {"claim": "case"},
    "activities": {
        "received": "Received",
        "triaged": "Triaged",
        "assessed": "Assessed",
        "decision_recorded": "Decision recorded",
        "information_requested": "Information requested",
        "information_received": "Information received",
        "reopened": "Reopened",
        "payment_recorded": "Payment recorded",
        "closed": "Closed",
    },
}

CSV = """case,activity,time
normal,Received,2026-01-01T00:00:00Z
normal,Triaged,2026-01-01T01:00:00Z
normal,Assessed,2026-01-01T02:00:00Z
normal,Decision recorded,2026-01-01T03:00:00Z
normal,Payment recorded,2026-01-01T04:00:00Z
normal,Closed,2026-01-01T05:00:00Z
slow,Received,2026-01-01T00:00:00Z
slow,Triaged,2026-01-04T00:00:00Z
slow,Information requested,2026-01-05T00:00:00Z
slow,Information received,2026-01-08T00:00:00Z
slow,Assessed,2026-01-09T00:00:00Z
slow,Decision recorded,2026-02-10T00:00:00Z
repeat,Received,2026-01-01T00:00:00Z
repeat,Triaged,2026-01-01T01:00:00Z
repeat,Assessed,2026-01-01T02:00:00Z
repeat,Decision recorded,2026-01-01T03:00:00Z
repeat,Decision recorded,2026-01-01T04:00:00Z
repeat,Reopened,2026-01-01T05:00:00Z
pending,Received,2026-01-01T00:00:00Z
pending,Triaged,2026-01-01T01:00:00Z
pending,Assessed,2026-01-01T02:00:00Z
out_of_order,Received,2026-01-01T00:00:00Z
out_of_order,Decision recorded,2026-01-02T00:00:00Z
out_of_order,Triaged,2026-01-03T00:00:00Z
out_of_order,Assessed,2026-01-04T00:00:00Z
"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _import() -> str:
    return workspace.import_log(
        BytesIO(CSV.encode()), name="Synthetic claims", source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"},
    )["log_id"]


def _install(log_id, *, mapping=None, parameters=None):
    return packs.install(log_id, PACK, {
        "version": "1.0.0", "mapping": copy.deepcopy(mapping if mapping is not None else MAPPING),
        "parameters": parameters if parameters is not None else {},
    })


def _rule(report, template, a, b=None):
    return next(row for section in report["compliance"] for row in section["results"]
                if row["rule"]["template"] == template and row["rule"]["a"] == a
                and row["rule"].get("b") == b)


def _cases(rule):
    return {row["case_id"] for row in rule["violating_case_examples"]}


def test_pack_is_discoverable_and_uses_existing_contract():
    entry = next(p for p in packs.catalog()["packs"] if p["pack_id"] == PACK)
    assert entry["versions"] == ["1.0.0"]
    assert entry["definitions"] == {
        "metrics": 6, "compliance_rules": 11, "object_rules": 0, "reference_model_types": 1,
    }
    defined = packs.definitions(PACK)
    assert {r["role"] for r in defined["activities"] if r["required"]} == {
        "received", "triaged", "assessed", "decision_recorded",
    }
    assert "claimant" not in str(defined["metrics"]).lower()
    assert all(item["case_type"] == "@claim" for item in defined["metrics"])


def test_real_saved_log_install_report_and_explicit_unknowns():
    log = _import()
    saved = _install(log)
    assert saved["change"] == "installed" and saved["not_applicable"] == []
    report = packs.report(log, PACK)
    assert report["pack"]["definitions_sha256"] == saved["definitions_sha256"]
    for key in ("source_system_validated", "policy_compliance_verified", "execution_authorized",
                "verified_business_workflow"):
        assert report[key] is False
    assert _cases(_rule(report, "existence", "Decision recorded")) == {"repeat", "pending"}
    assert "slow" in _cases(_rule(report, "response_within", "Received", "Triaged"))
    assert "slow" in _cases(_rule(report, "response_within", "Received", "Decision recorded"))
    assert "out_of_order" in _cases(_rule(report, "precedence", "Assessed", "Decision recorded"))
    assert _cases(_rule(report, "existence", "Reopened")) == {"repeat"}
    metrics = {item["name"]: item for item in report["metrics"]}
    assert metrics["claims_time_to_decision"]["coverage"]["cases_without_to_activity"] == 1
    assert metrics["claims_payment_records"]["mean"] == pytest.approx(0.2)
    assert metrics["claims_information_requests"]["mean"] == pytest.approx(0.2)
    assert report["object_model"] is not None
    assert "not verified" in " ".join(report["assumptions"]).lower() or any(
        "does not prove" in item.lower() for item in report["assumptions"])


def test_unmapped_optional_steps_are_explicit_and_never_reclassified_as_complete():
    log = _import()
    mapping = copy.deepcopy(MAPPING)
    for role in ("information_requested", "information_received", "reopened",
                 "payment_recorded", "closed"):
        mapping["activities"].pop(role)
    saved = _install(log, mapping=mapping)
    assert len(saved["metric_ids"]) == 3
    missing = {row["unmapped_role"] for row in saved["not_applicable"]}
    assert {"information_requested", "information_received", "reopened",
            "payment_recorded", "closed"} <= missing
    report = packs.report(log, PACK)
    assert len(report["metrics"]) == 3
    assert "claims_payment_records" not in {m["name"] for m in report["metrics"]}
    assert report["source_system_validated"] is False


def test_owner_changes_targets_and_uninstall_only_removes_pack_metrics():
    log = _import()
    query.save_metric(log, {"metric": {
        "name": "owner_defined", "kind": "activity_count", "activity": "Triaged",
    }})
    first = _install(log)
    assert len(first["metric_ids"]) == 6
    later = _install(log, parameters={"triage_hours": 96, "decision_days": 60})
    assert later["definitions_sha256"] != first["definitions_sha256"]
    report = packs.report(log, PACK)
    assert _rule(report, "response_within", "Received", "Triaged")["rule"]["seconds"] == 96 * 3600
    assert _rule(report, "response_within", "Received", "Decision recorded")["rule"]["seconds"] == 60 * 86400
    assert "slow" not in _cases(_rule(report, "response_within", "Received", "Triaged"))
    assert packs.uninstall(log, PACK)["metrics_removed"] == 6
    assert [m["metric"]["name"] for m in query.list_metrics(log)["metrics"]] == ["owner_defined"]
    with pytest.raises(workspace.EventLogWorkspaceError):
        packs.report(log, PACK)


@pytest.mark.parametrize("bad", [
    {"triage_hours": 0}, {"triage_hours": 8761}, {"decision_days": 0},
    {"decision_days": 731}, {"triage_hours": float("nan")},
    {"decision_days": float("inf")}, {"autorun_payment": 1},
])
def test_invalid_parameters_cannot_mutate_installation(bad):
    log = _import()
    with pytest.raises(workspace.EventLogWorkspaceError):
        _install(log, parameters=bad)
    assert packs.installed(log)["installs"] == []


def test_required_roles_and_imported_activity_names_are_enforced():
    log = _import()
    mapping = copy.deepcopy(MAPPING)
    del mapping["activities"]["decision_recorded"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="required role decision_recorded"):
        _install(log, mapping=mapping)
    mapping["activities"]["decision_recorded"] = "Does not exist"
    with pytest.raises(workspace.EventLogWorkspaceError, match="not an activity"):
        _install(log, mapping=mapping)
    assert packs.installed(log)["installs"] == []
