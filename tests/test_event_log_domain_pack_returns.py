"""Returns/refunds process pack: real event-log engine, no inferred payment/approval."""
from __future__ import annotations

import copy
from io import BytesIO

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace

PACK = "returns_refunds"
MAPPING = {"object_types": {"return_case": "case"}, "activities": {
    "request": "Requested", "receive": "Received", "inspect": "Inspected",
    "approve": "Approved", "reject": "Rejected", "refund": "Refunded", "close": "Closed",
}}
CSV = """case,activity,time
normal,Requested,2026-01-01T00:00:00Z
normal,Received,2026-01-02T00:00:00Z
normal,Inspected,2026-01-02T01:00:00Z
normal,Approved,2026-01-03T00:00:00Z
normal,Refunded,2026-01-04T00:00:00Z
normal,Closed,2026-01-05T00:00:00Z
rejected,Requested,2026-01-01T00:00:00Z
rejected,Inspected,2026-01-02T00:00:00Z
rejected,Rejected,2026-01-02T01:00:00Z
late_assessment,Requested,2026-01-01T00:00:00Z
late_assessment,Inspected,2026-01-10T00:00:00Z
late_assessment,Rejected,2026-01-11T00:00:00Z
late_refund,Requested,2026-01-01T00:00:00Z
late_refund,Inspected,2026-01-02T00:00:00Z
late_refund,Approved,2026-01-03T00:00:00Z
late_refund,Refunded,2026-01-25T00:00:00Z
pending,Requested,2026-01-01T00:00:00Z
pending,Inspected,2026-01-02T00:00:00Z
pending,Approved,2026-01-03T00:00:00Z
repeat,Requested,2026-01-01T00:00:00Z
repeat,Inspected,2026-01-02T00:00:00Z
repeat,Approved,2026-01-03T00:00:00Z
repeat,Refunded,2026-01-04T00:00:00Z
repeat,Refunded,2026-01-05T00:00:00Z
out_of_order,Requested,2026-01-01T00:00:00Z
out_of_order,Inspected,2026-01-02T00:00:00Z
out_of_order,Refunded,2026-01-03T00:00:00Z
out_of_order,Approved,2026-01-04T00:00:00Z
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def imported():
    return workspace.import_log(BytesIO(CSV.encode()), name="Synthetic returned items",
        source_format="csv", mapping={"case_column": "case", "activity_column": "activity",
                                      "timestamp_column": "time"})["log_id"]


def install(log_id, mapping=None, parameters=None):
    return packs.install(log_id, PACK, {"version": "1.0.0", "mapping": mapping or MAPPING,
                                       "parameters": parameters or {}})


def rule(report, template, a, b=None):
    return next(r for group in report["compliance"] for r in group["results"]
                if r["rule"]["template"] == template and r["rule"]["a"] == a
                and r["rule"].get("b") == b)


def examples(result):
    return {row["case_id"] for row in result["violating_case_examples"]}


def test_catalog_install_and_planted_exception_evidence():
    entry = next(p for p in packs.catalog()["packs"] if p["pack_id"] == PACK)
    assert entry["definitions"] == {
        "metrics": 5, "compliance_rules": 8, "object_rules": 2, "reference_model_types": 2}
    log_id = imported()
    saved = install(log_id)
    report = packs.report(log_id, PACK)
    assert saved["definitions_sha256"] == report["pack"]["definitions_sha256"]
    for flag in ("source_system_validated", "policy_compliance_verified",
                 "verified_business_workflow", "execution_authorized"):
        assert report[flag] is False
    assert examples(rule(report, "response_within", "Requested", "Inspected")) == {"late_assessment"}
    refund_due = rule(report, "response_within", "Approved", "Refunded")
    assert {"late_refund", "pending", "out_of_order"} <= examples(refund_due)
    # A rejected return with no recorded approval is never treated as overdue for a refund.
    assert "rejected" not in examples(refund_due)
    assert "late_assessment" not in examples(refund_due)
    assert examples(rule(report, "precedence", "Approved", "Refunded")) == {"out_of_order"}
    assert examples(rule(report, "existence", "Refunded")) == {"repeat"}
    metrics = {m["name"]: m for m in report["metrics"]}
    assert metrics["returns_refund_records"]["mean"] == pytest.approx(5 / 7)
    assert metrics["returns_rejections"]["mean"] == pytest.approx(2 / 7)
    assert metrics["returns_request_to_refund"]["coverage"]["cases_without_to_activity"] == 3


def test_optional_roles_and_reconfiguration_do_not_touch_manual_metrics():
    log_id = imported()
    reduced = copy.deepcopy(MAPPING)
    for role in ("approve", "reject", "refund", "close", "receive"):
        del reduced["activities"][role]
    saved = install(log_id, mapping=reduced)
    assert len(saved["metric_ids"]) == 1
    assert {entry["unmapped_role"] for entry in saved["not_applicable"]} >= {
        "approve", "reject", "refund", "close"}
    query.save_metric(log_id, {"metric": {"name": "manual_review_count", "kind": "activity_count",
                                          "activity": "Inspected"}})
    changed = install(log_id, parameters={"assessment_days": 14, "refund_days": 30})
    assert changed["definitions_sha256"] != saved["definitions_sha256"]
    assert rule(packs.report(log_id, PACK), "response_within", "Requested", "Inspected")["violating_cases"] == 0
    assert packs.uninstall(log_id, PACK)["metrics_removed"] == 5
    assert [m["metric"]["name"] for m in query.list_metrics(log_id)["metrics"]] == ["manual_review_count"]


@pytest.mark.parametrize("bad", [
    {"refund_days": 0}, {"assessment_days": 366}, {"refund_days": float("nan")},
    {"refund_days": float("inf")}, {"approve_without_review": 1},
])
def test_invalid_parameter_rejected_atomically(bad):
    log_id = imported()
    with pytest.raises(workspace.EventLogWorkspaceError):
        install(log_id, parameters=bad)
    assert packs.installed(log_id)["installs"] == []


def test_unknown_required_mapping_is_rejected_without_install():
    log_id = imported()
    for role in ("request", "inspect"):
        mapping = copy.deepcopy(MAPPING)
        del mapping["activities"][role]
        with pytest.raises(workspace.EventLogWorkspaceError, match="required role"):
            install(log_id, mapping=mapping)
    assert packs.installed(log_id)["installs"] == []
