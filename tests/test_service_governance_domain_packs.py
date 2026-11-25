"""Synthetic acceptance for R23 service-governance packs.

The fixture uses literal source labels different from the published labels, and
only pseudonymous case IDs, timestamps and synthetic resource labels.
"""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import re

import pytest

from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from server.event_log_domain_packs_service_governance import SERVICE_GOVERNANCE_PACKS


CASES = [
    ("change_control", "change", "requested", "assessed", "validated",
     "assessment_hours", "chg_request_to_assessment", "reworked"),
    ("customer_complaints", "complaint", "received", "acknowledged", "response_sent",
     "acknowledgement_hours", "complaint_intake_to_acknowledgement", "escalated"),
    ("data_access_requests", "request", "received", "verified", "response_sent",
     "verification_hours", "dar_intake_to_verification", "clarification"),
]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _source_label(role):
    return "Source::" + role


def _spec(pack_id, case_role, *, optional=True):
    definition = packs.definitions(pack_id)
    return {
        "version": "1.0.0",
        "mapping": {
            "object_types": {case_role: "case"},
            "activities": {r["role"]: _source_label(r["role"])
                           for r in definition["activities"] if optional or r["required"]},
        },
    }


def _fixture(pack_id):
    definition = packs.definitions(pack_id)
    required = [r["role"] for r in definition["activities"] if r["required"]]
    optional = [r["role"] for r in definition["activities"] if not r["required"]]
    target = definition["parameters"][0]["default"]
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["record", "event", "time", "actor"])

    def emit(case, roles, hours, actors=None):
        for i, (role, hour) in enumerate(zip(roles, hours)):
            writer.writerow([case, _source_label(role), (base + timedelta(hours=hour)).isoformat(),
                             actors[i] if actors is not None else ("alpha" if i == 0 else "beta")])

    # An exact reference case, an observed late first response, a truncated
    # case, and an explicitly out-of-order case. All timestamps are distinct.
    emit("good", required, range(len(required)))
    emit("late", required, [0] + [target + 1 + i for i in range(len(required) - 1)])
    emit("open", required[:1], [0])
    emit("reverse", [required[1], required[0], *required[2:]], range(len(required)))
    emit("extras", [*required, *optional], range(len(required) + len(optional)))
    return workspace.import_log(
        BytesIO(buffer.getvalue().encode()), name="Governance fixture", source_format="csv",
        mapping={"case_column": "record", "activity_column": "event",
                 "timestamp_column": "time", "resource_column": "actor"},
    )["log_id"]


def _rule(report, template, a, b=None):
    return next(result for group in report["compliance"] for result in group["results"]
                if result["rule"]["template"] == template
                and result["rule"]["a"] == _source_label(a)
                and result["rule"].get("b") == (_source_label(b) if b else None))


@pytest.mark.parametrize("pack_id,case_role,start,first,last,target,metric,optional", CASES)
def test_real_import_install_and_report(pack_id, case_role, start, first, last, target, metric, optional):
    log = _fixture(pack_id)
    spec = _spec(pack_id, case_role)
    installed = packs.install(log, pack_id, spec)
    assert installed["change"] == "installed"
    assert installed["not_applicable"] == []
    report = packs.report(log, pack_id)
    assert report["source_system_validated"] is False
    assert report["policy_compliance_verified"] is False
    assert report["pack"]["definitions_sha256"] == installed["definitions_sha256"]
    assert report["object_model"]["summary"]["fitting_objects"] >= 1
    metrics = {m["name"]: m for m in report["metrics"]}
    assert metrics[metric]["coverage"]["cases_without_to_activity"] >= 1
    within = _rule(report, "response_within", start, first)
    examples = {e["case_id"] for e in within["violating_case_examples"]}
    assert {"late", "open", "reverse"} <= examples
    precedence = _rule(report, "precedence", start, first)
    assert {e["case_id"] for e in precedence["violating_case_examples"]} == {"reverse"}
    # Change a target, leaving the original recorded events untouched.
    changed = copy.deepcopy(spec)
    changed["parameters"] = {target: .25}
    updated = packs.install(log, pack_id, changed)
    assert updated["definitions_sha256"] != installed["definitions_sha256"]
    new_rule = _rule(packs.report(log, pack_id), "response_within", start, first)
    assert new_rule["rule"]["seconds"] == 900
    assert "good" in {e["case_id"] for e in new_rule["violating_case_examples"]}
    # Only pack-owned metrics are removed; a user-created metric survives.
    query.save_metric(log, {"metric": {"name": "custom_count", "kind": "activity_count",
                                       "activity": _source_label(start)}})
    packs.uninstall(log, pack_id)
    assert [row["metric"]["name"] for row in query.list_metrics(log)["metrics"]] == ["custom_count"]


@pytest.mark.parametrize("pack_id,case_role,start,first,last,target,metric,optional", CASES)
def test_optional_mapping_and_required_role_refusal(pack_id, case_role, start, first, last, target, metric, optional):
    log = _fixture(pack_id)
    spec = _spec(pack_id, case_role, optional=False)
    broken = copy.deepcopy(spec)
    broken["mapping"]["activities"].pop(first)
    with pytest.raises(workspace.EventLogWorkspaceError, match="required role"):
        packs.install(log, pack_id, broken)
    assert packs.installed(log)["installs"] == []
    installed = packs.install(log, pack_id, spec)
    missing = {row["unmapped_role"] for row in installed["not_applicable"]}
    assert optional in missing
    assert packs.report(log, pack_id)["not_applicable"] == installed["not_applicable"]


def test_catalog_contract_and_unique_metric_names():
    catalog = {p["pack_id"]: p for p in packs.catalog()["packs"]}
    seen = set()
    for pack_id in SERVICE_GOVERNANCE_PACKS:
        definition = packs.definitions(pack_id)
        assert catalog[pack_id]["latest_version"] == "1.0.0"
        assert len(definition["compliance"]) <= 20
        roles = {r["role"] for r in definition["activities"]}
        assert len(roles) == len(definition["activities"])
        case_role = definition["object_types"][0]["role"]
        assert case_role not in roles
        for item in definition["metrics"]:
            name = item["metric"]["name"]
            assert re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name)
            assert name not in seen
            seen.add(name)
        spec = _spec(pack_id, case_role)
        resolved = packs.resolve(definition, spec["mapping"],
                                 {p["name"]: p["default"] for p in definition["parameters"]})
        assert resolved["not_applicable"] == []
        assert len(resolved["metrics"]) == catalog[pack_id]["definitions"]["metrics"]
        assert all(r["case_type"] == "case" for r in resolved["compliance"])
        # Catalog callers cannot mutate the installed definition.
        definition["metrics"].clear()
        assert packs.definitions(pack_id)["metrics"]
