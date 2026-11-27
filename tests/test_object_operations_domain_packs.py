"""Real retained OCEL import and report regression for object operations packs."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

import pytest

from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from server.event_log_domain_packs_object_operations import OBJECT_OPERATIONS_PACKS


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def fixture(key, *, cross=False):
    pack = packs.definitions(key)
    parent, child = [r["role"] for r in pack["object_types"]]
    types = {parent: "Source parent", child: "Source child"}
    activities = {r["role"]: "System: " + r["role"] for r in pack["activities"]}
    params = {r["name"]: r["default"] for r in pack["parameters"]}
    objects, events = [], []
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def event(eid, role, hour, ids):
        events.append({"id": eid, "type": activities[role],
                       "time": (t + timedelta(hours=hour)).isoformat(),
                       "relationships": [{"objectId": i, "qualifier": ""} for i in ids]})

    # Explicit two-child membership and one independent missing check.
    for name in ("good", "missing", "late", "open"):
        kids = [name + "-a", name + "-b"]
        objects.append({"id": name, "type": types[parent]})
        objects.extend({"id": kid, "type": types[child]} for kid in kids)
        event(name + "-open", "opened", 0, [name, *kids])
        if name == "open":
            continue
        check_hour = params["check_hours"] + 1 if name == "late" else 1
        event(name + "-check-a", "checked", check_hour, [kids[0]])
        if name != "missing":
            event(name + "-check-b", "checked", check_hour + .25, [kids[1]])
        if name == "good":
            event(name + "-exception", "exception", 2, [kids[1]])
        closing_kids = kids
        if cross and name in ("good", "late"):
            closing_kids = [kids[0], ("late" if name == "good" else "good") + "-b"]
        close_hour = params["close_hours"] + 2 if name == "late" else 3
        event(name + "-close", "closed", close_hour, [name, *closing_kids])

    doc = {"objectTypes": [{"name": n, "attributes": []} for n in types.values()],
           "eventTypes": [{"name": n, "attributes": []} for n in activities.values()],
           "objects": objects, "events": events}
    log = workspace.import_log(BytesIO(json.dumps(doc).encode()), name=key,
                               source_format="ocel")["log_id"]
    return log, {"version": "1.0.0", "mapping": {"object_types": types, "activities": activities}}


def compliance(report, case_type, template, a, b=None):
    return next(row for group in report["compliance"] if group["case_type"] == case_type
                for row in group["results"] if row["rule"]["template"] == template
                and row["rule"]["a"] == a and row["rule"].get("b") == b)


def object_rule(report, template, **fields):
    return next(r for r in report["object_rules"]["results"]
                if r["rule"]["template"] == template
                and all(r["rule"].get(k) == v for k, v in fields.items()))


@pytest.mark.parametrize("key", sorted(OBJECT_OPERATIONS_PACKS))
def test_exact_parent_child_reports(key):
    log, spec = fixture(key)
    assert key in {p["pack_id"] for p in packs.catalog()["packs"]}
    installed = packs.install(log, key, spec)
    report = packs.report(log, key)
    assert report["pack"]["definitions_sha256"] == installed["definitions_sha256"]
    assert report["source_system_validated"] is False
    assert report["policy_compliance_verified"] is False
    assert report["not_applicable"] == []
    assert len(report["metrics"]) == 5
    parent_deadline = compliance(report, "Source parent", "response_within", "System: opened", "System: closed")
    assert parent_deadline["activated_cases"] == 4
    assert parent_deadline["violating_cases"] == 2
    assert {e["case_id"] for e in parent_deadline["violating_case_examples"]} == {"late", "open"}
    child_deadline = compliance(report, "Source child", "response_within", "System: opened", "System: checked")
    assert child_deadline["activated_cases"] == 8
    assert child_deadline["violating_cases"] == 5
    assert {e["case_id"] for e in child_deadline["violating_case_examples"]} == {
        "late-a", "late-b", "missing-b", "open-a", "open-b"}
    sync = object_rule(report, "synchronization")
    assert sync["complete"] is True and sync["violating"] == 1
    assert object_rule(report, "object_cardinality", object_type="Source child",
                       via_activity="System: opened")["violating"] == 0
    assert object_rule(report, "object_cardinality", object_type="Source child",
                       via_activity="System: closed")["violating"] == 2
    assert report["object_model"]["summary"]["complete"] is True
    assert report["object_model"]["summary"]["objects"] == 12

    # Reconfiguration changes the actual evaluated rule, not just its UI label.
    spec["parameters"] = {"check_hours": .25}
    updated = packs.install(log, key, spec)
    assert updated["definitions_sha256"] != installed["definitions_sha256"]
    strict = compliance(packs.report(log, key), "Source child", "response_within",
                        "System: opened", "System: checked")
    assert strict["rule"]["seconds"] == 900 and strict["violating_cases"] == 8

    # Optional omission remains disclosed, with no silent empty metric.
    del spec["mapping"]["activities"]["exception"]
    packs.install(log, key, spec)
    omitted = packs.report(log, key)
    assert "exception" in {r["unmapped_role"] for r in omitted["not_applicable"]}
    assert len(omitted["metrics"]) == 4
    packs.uninstall(log, key)
    with pytest.raises(workspace.EventLogWorkspaceError):
        packs.report(log, key)


@pytest.mark.parametrize("key", sorted(OBJECT_OPERATIONS_PACKS))
def test_cross_parent_close_is_not_hidden_by_per_event_cardinality(key):
    log, spec = fixture(key, cross=True)
    packs.install(log, key, spec)
    report = packs.report(log, key)
    # Each child has one opening parent and each closed child one closing parent,
    # but two children link to DIFFERENT parents across the retained events.
    exact = object_rule(report, "object_cardinality", object_type="Source child", via_activity=None)
    assert exact["violating"] == 2
    assert object_rule(report, "object_cardinality", object_type="Source child",
                       via_activity="System: opened")["violating"] == 0
    assert object_rule(report, "event_cardinality", activity="System: closed",
                       object_type="Source parent")["violating"] == 0


@pytest.mark.parametrize("key", sorted(OBJECT_OPERATIONS_PACKS))
def test_required_object_role_cannot_be_omitted(key):
    log, spec = fixture(key)
    child = next(iter(reversed(spec["mapping"]["object_types"])))
    del spec["mapping"]["object_types"][child]
    with pytest.raises(workspace.EventLogWorkspaceError, match="required role"):
        packs.install(log, key, spec)
    assert packs.installed(log)["installs"] == []
