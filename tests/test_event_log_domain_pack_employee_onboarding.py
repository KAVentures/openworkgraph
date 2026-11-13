"""R23: installable employee-onboarding domain pack over a synthetic case log."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace

PACK = "employee_onboarding"
MAPPING = {
    "object_types": {"onboarding_case": "case"},
    "activities": {
        "onboarding_started": "Request opened",
        "manager_approved": "Manager approved",
        "identity_provisioned": "Access recorded",
        "training_completed": "Training logged",
        "access_reviewed": "Access reviewed",
        "work_started": "Started work",
        "case_closed": "Onboarding closed",
    },
}
CSV = """case,activity,time
O1,Request opened,2026-01-01T00:00:00Z
O1,Manager approved,2026-01-01T01:00:00Z
O1,Access recorded,2026-01-01T02:00:00Z
O1,Training logged,2026-01-01T03:00:00Z
O1,Access reviewed,2026-01-01T04:00:00Z
O1,Started work,2026-01-01T05:00:00Z
O1,Onboarding closed,2026-01-01T06:00:00Z
O2,Request opened,2026-01-01T00:00:00Z
O2,Manager approved,2026-01-01T01:00:00Z
O2,Access recorded,2026-01-03T12:00:00Z
O2,Started work,2026-01-04T00:00:00Z
O3,Request opened,2026-01-01T00:00:00Z
O3,Started work,2026-01-02T00:00:00Z
O4,Access recorded,2026-01-01T00:00:00Z
O4,Request opened,2026-01-01T01:00:00Z
O4,Started work,2026-01-01T02:00:00Z
O5,Request opened,2026-01-01T00:00:00Z
O5,Access recorded,2026-01-01T01:00:00Z
O5,Access recorded,2026-01-01T02:00:00Z
O5,Started work,2026-01-01T03:00:00Z
O6,Request opened,2026-01-01T00:00:00Z
O6,Manager approved,2026-01-01T01:00:00Z
O6,Access recorded,2026-01-01T02:00:00Z
O6,Started work,2026-01-01T03:00:00Z
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def imported():
    return workspace.import_log(
        BytesIO(CSV.encode("utf-8")), name="Synthetic onboarding", source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
    )["log_id"]


def _install(log_id, mapping=MAPPING, parameters=None):
    return packs.install(log_id, PACK, {
        "version": "1.0.0", "mapping": mapping,
        **({"parameters": parameters} if parameters is not None else {}),
    })


def _rule(report, template, a, b=None):
    return next(
        x for section in report["compliance"] for x in section["results"]
        if x["rule"]["template"] == template and x["rule"]["a"] == a
        and x["rule"].get("b") == b
    )


def test_catalog_has_versioned_installable_onboarding_pack_and_bounded_definitions():
    entry = next(p for p in packs.catalog()["packs"] if p["pack_id"] == PACK)
    assert entry["title"] == "Employee onboarding and access readiness"
    assert entry["versions"] == ["1.0.0"]
    assert entry["definitions"] == {"metrics": 5, "compliance_rules": 10,
                                     "object_rules": 0, "reference_model_types": 1}
    assert {r["role"] for r in entry["object_types"] if r["required"]} == {"onboarding_case"}
    assert {r["role"] for r in entry["activities"] if r["required"]} == {
        "onboarding_started", "identity_provisioned", "work_started"
    }
    assert any("does NOT verify" in s for s in entry["assumptions"])
    assert packs.definitions(PACK)["reference_model"]["types"][0]["object_type"] == "@onboarding_case"


def test_report_marks_missing_late_out_of_order_and_repeated_evidence():
    log = imported()
    installed = _install(log)
    assert installed["change"] == "installed"
    assert installed["not_applicable"] == []
    assert len(installed["metric_ids"]) == 5
    report = packs.report(log, PACK)
    assert report["pack"]["definitions_sha256"] == installed["definitions_sha256"]
    assert report["source_system_validated"] is False
    assert report["policy_compliance_verified"] is False
    assert report["verified_business_workflow"] is False
    assert report["execution_authorized"] is False
    measures = {m["name"]: m for m in report["metrics"]}
    assert set(measures) == {
        "onboarding_request_to_provision", "onboarding_request_to_start",
        "onboarding_provision_to_start", "onboarding_provision_records",
        "onboarding_training_records",
    }
    assert measures["onboarding_request_to_provision"]["cases"] == 6
    assert measures["onboarding_request_to_provision"]["coverage"]["cases_without_to_activity"] >= 1
    assert measures["onboarding_training_records"]["mean"] == pytest.approx(1 / 6, abs=1e-6)
    repeated = _rule(report, "existence", "Access recorded")
    assert repeated["violating_cases"] == 2
    assert {x["case_id"] for x in repeated["violating_case_examples"]} == {"O3", "O5"}
    assert _rule(report, "precedence", "Request opened", "Access recorded")["violating_cases"] >= 1
    response = _rule(report, "response_within", "Request opened", "Access recorded")
    assert response["rule"]["seconds"] == 48 * 3600
    assert {"O2", "O3"}.issubset({x["case_id"] for x in response["violating_case_examples"]})
    duties = _rule(report, "segregation_of_duties", "Request opened", "Manager approved")
    assert duties["unknown_cases"] > 0
    assert duties["satisfied_cases"] == 0
    assert report["object_model"]["summary"]["objects"] == 6
    assert any("does NOT verify" in s for s in report["assumptions"])


def test_unmapped_optional_steps_are_not_misinterpreted_as_failed_controls():
    log = imported()
    mapping = {"object_types": MAPPING["object_types"], "activities": {
        k: MAPPING["activities"][k] for k in ("onboarding_started", "identity_provisioned", "work_started")
    }}
    result = _install(log, mapping=mapping)
    assert len(result["metric_ids"]) == 4
    assert {x["unmapped_role"] for x in result["not_applicable"]} == {
        "manager_approved", "training_completed", "access_reviewed", "case_closed"
    }
    report = packs.report(log, PACK)
    assert len(report["metrics"]) == 4
    assert len(report["compliance"][0]["results"]) == 5
    assert {x["rule"]["template"] for x in report["compliance"][0]["results"]} == {
        "response_within", "precedence", "existence"
    }


def test_reconfigured_target_changes_elapsed_rule_but_not_missing_event():
    log = imported()
    first = _install(log)
    before = _rule(packs.report(log, PACK), "response_within", "Request opened", "Access recorded")
    assert before["rule"]["seconds"] == 48 * 3600
    changed = _install(log, parameters={"provisioning_hours": 72, "start_days": 30})
    assert changed["change"] == "reconfigured"
    assert changed["definitions_sha256"] != first["definitions_sha256"]
    assert len(query.list_metrics(log)["metrics"]) == 5
    after = _rule(packs.report(log, PACK), "response_within", "Request opened", "Access recorded")
    assert after["rule"]["seconds"] == 72 * 3600
    failing = {x["case_id"] for x in after["violating_case_examples"]}
    assert "O2" not in failing and "O3" in failing


def test_refuses_missing_mapping_invalid_source_or_nonfinite_targets():
    log = imported()
    with pytest.raises(workspace.EventLogWorkspaceError, match="map the required role"):
        _install(log, mapping={"object_types": MAPPING["object_types"],
                               "activities": {"onboarding_started": "Request opened",
                                              "identity_provisioned": "Access recorded"}})
    with pytest.raises(workspace.EventLogWorkspaceError, match="not an activity of this log"):
        _install(log, mapping={"object_types": MAPPING["object_types"],
                               "activities": {**MAPPING["activities"], "work_started": "Not in log"}})
    with pytest.raises(workspace.EventLogWorkspaceError, match="from 0.25 to 8760"):
        _install(log, parameters={"provisioning_hours": 0})
    with pytest.raises(workspace.EventLogWorkspaceError, match="no parameter"):
        _install(log, parameters={"create_accounts": 1})
    for value in (float("inf"), float("nan"), -float("inf")):
        with pytest.raises(workspace.EventLogWorkspaceError, match="domain pack install"):
            _install(log, parameters={"provisioning_hours": value})
    assert packs.installed(log)["installs"] == []
