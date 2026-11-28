"""Independent OCEL parent/child fixtures for the regulated object pack suite."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

import pytest

from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace
from server.event_log_domain_packs_regulated_services import REGULATED_OBJECT_PACKS


CASES = {
    "audit_engagement_evidence_items": ("engagement", "evidence_item", 168, 720),
    "utility_meter_reading_batch": ("reading_batch", "reading", 24, 72),
    "document_digitization_batch": ("digitization_batch", "document_item", 48, 168),
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def fixture(key, crossed=False):
    parent, child, check, close = CASES[key]
    types = {parent: "Source group", child: "Source member"}
    names = {"opened": "Open, recorded", "checked": "Check, recorded",
             "exception": "Exception, recorded", "closed": "Close, recorded"}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    objects, events = [], []

    def event(eid, role, hour, linked):
        events.append({"id": eid, "type": names[role],
                       "time": (start + timedelta(hours=hour)).isoformat(),
                       "relationships": [{"objectId": i, "qualifier": ""} for i in linked]})

    for group in ("normal", "late", "missing", "open"):
        kids = [group + "-a", group + "-b"]
        objects.append({"id": group, "type": types[parent]})
        objects.extend({"id": k, "type": types[child]} for k in kids)
        event(group + "-open", "opened", 0, [group, *kids])
        if group == "open":
            continue
        hour = check + 1 if group == "late" else 1
        event(group + "-a-check", "checked", hour, [kids[0]])
        if group != "missing":
            event(group + "-b-check", "checked", hour + .25, [kids[1]])
        if group == "normal":
            event(group + "-exception", "exception", 2, [kids[1]])
        if crossed and group in ("normal", "late"):
            kids[1] = ("late" if group == "normal" else "normal") + "-b"
        event(group + "-close", "closed", close + 2 if group == "late" else 3,
              [group, *kids])
    payload = {"objectTypes": [{"name": t, "attributes": []} for t in types.values()],
               "eventTypes": [{"name": a, "attributes": []} for a in names.values()],
               "objects": objects, "events": events}
    log = workspace.import_log(BytesIO(json.dumps(payload).encode()), name=key,
                               source_format="ocel")["log_id"]
    return log, {"version": "1.0.0", "mapping": {
        "object_types": types, "activities": names}}


def deadline(report, case_type):
    return next(row for group in report["compliance"] if group["case_type"] == case_type
                for row in group["results"] if row["rule"]["template"] == "response_within")


def object_rule(report, template, **fields):
    return next(row for row in report["object_rules"]["results"]
                if row["rule"]["template"] == template
                and all(row["rule"].get(k) == v for k, v in fields.items()))


@pytest.mark.parametrize("key", CASES)
def test_independent_parent_and_child_population(key):
    log, spec = fixture(key)
    installed = packs.install(log, key, spec)
    report = packs.report(log, key)
    assert installed["definitions_sha256"] == report["pack"]["definitions_sha256"]
    assert report["source_system_validated"] is False
    assert report["policy_compliance_verified"] is False
    assert report["execution_authorized"] is False
    assert len(report["metrics"]) == 5
    parent = deadline(report, "Source group")
    child = deadline(report, "Source member")
    assert parent["activated_cases"] == 4 and parent["violating_cases"] == 2
    assert child["activated_cases"] == 8 and child["violating_cases"] == 5
    assert {r["case_id"] for r in child["violating_case_examples"]} == {
        "late-a", "late-b", "missing-b", "open-a", "open-b"}
    assert object_rule(report, "synchronization")["violating"] == 1
    assert report["object_model"]["summary"]["objects"] == 12
    spec["parameters"] = {"check_hours": .25}
    changed = packs.install(log, key, spec)
    assert changed["definitions_sha256"] != installed["definitions_sha256"]
    assert deadline(packs.report(log, key), "Source member")["violating_cases"] == 8
    del spec["mapping"]["activities"]["exception"]
    packs.install(log, key, spec)
    report = packs.report(log, key)
    assert "exception" in {r["unmapped_role"] for r in report["not_applicable"]}
    assert len(report["metrics"]) == 4
    packs.uninstall(log, key)
    with pytest.raises(workspace.EventLogWorkspaceError):
        packs.report(log, key)


@pytest.mark.parametrize("key", CASES)
def test_cross_parent_links_detected_even_when_event_cardinality_is_valid(key):
    log, spec = fixture(key, crossed=True)
    packs.install(log, key, spec)
    report = packs.report(log, key)
    assert object_rule(report, "event_cardinality", activity="Close, recorded",
                       object_type="Source group")["violating"] == 0
    assert object_rule(report, "object_cardinality", object_type="Source member",
                       via_activity=None)["violating"] == 2


@pytest.mark.parametrize("key", CASES)
def test_missing_child_mapping_refused(key):
    log, spec = fixture(key)
    del spec["mapping"]["object_types"][CASES[key][1]]
    with pytest.raises(workspace.EventLogWorkspaceError):
        packs.install(log, key, spec)
    assert packs.installed(log)["installs"] == []


def test_catalog_integration():
    assert set(CASES) == set(REGULATED_OBJECT_PACKS)
    assert set(CASES) <= {p["pack_id"] for p in packs.catalog()["packs"]}
