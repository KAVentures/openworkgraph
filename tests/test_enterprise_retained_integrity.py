"""Retained structural audit on real workspace transactions and synthetic feeds."""
from datetime import timedelta
import json

import httpx
import pytest

from server import enterprise_retained_integrity as integrity
from server import enterprise_rest_feed as rest
from server import enterprise_connector_snapshot as snapshot
from server import enterprise_ingestion_run_history as history
from server import event_log_workspace as ws

URL = "https://customer.example.com/events"
ENV = "OWG_REST_TOKEN_TEST"
TOKEN = "fixture-secret-token-123456"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS", json.dumps([URL]))
    monkeypatch.setenv(ENV, TOKEN)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES", raising=False)
    monkeypatch.delenv("OWG_REST_ENDPOINT_CREDENTIALS", raising=False)


def profile(name="Feed"):
    since = (rest._now() - timedelta(days=3)).replace(microsecond=0)
    return rest.create({"name": name, "endpoint": URL, "credential_env": ENV,
        "initial_since": rest._iso(since), "window_hours": 24, "overlap_minutes": 60,
        "page_size": 5, "mapping": {"event_id": "id", "case_id": "case",
            "activity": "step", "event_time": "time", "change_time": "changed", "resource": "actor"},
        "confirm_configuration": True, "confirm_activity_labels": True})


def run(p):
    at = rest._stamp(p["config"]["initial_since"]) + timedelta(hours=1)
    records = [{"id": "private-event-"+str(i), "case": "private-case-"+str(i),
                "step": "Private activity", "time": rest._iso(at+timedelta(seconds=i)),
                "changed": rest._iso(at+timedelta(seconds=i)), "actor": "private-actor"}
               for i in range(2)]
    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"records": records, "next_cursor": None})), **kwargs)
    return rest.run(p["profile_id"], p["revision"], confirm_reads=True,
                    confirm_retention=True, client_factory=factory)


def current(p):
    return next(x for x in rest.inventory()["profiles"] if x["profile_id"] == p["profile_id"])


def select(*profiles):
    return {"targets": [{"profile_id": p["profile_id"], "revision": p["revision"]} for p in profiles]}


def approve(payload, plan):
    return {**payload, "plan_sha256": plan["plan_sha256"], "confirm_exact_findings": True,
            "confirm_local_containment": True, "confirm_remote_coordination": True}


def mutate(sql, *args):
    with ws._LOCK:
        c = rest._connect()
        try:
            c.execute(sql, list(args))
        finally:
            c.close()


def audit(p, action="hold"):
    return integrity.inspect(select(p), action=action)["targets"][0]


def damage(p):
    mutate("UPDATE logs SET events=events+1 WHERE log_id=?", p["log_id"])


def test_normal_projection_and_no_source_access(monkeypatch):
    p = run(profile())
    monkeypatch.setattr(httpx.Client, "stream", lambda *a, **k: pytest.fail("unexpected provider read"))
    monkeypatch.setattr(rest, "_check_binding", lambda *a, **k: pytest.fail("unexpected credential check"))
    report = integrity.inspect(select(p))
    row = report["targets"][0]
    assert row["status"] == "structurally_consistent"
    assert row["violations"] == {}
    assert row["counts"]["events"] == row["counts"]["event_source_keys"] == 2
    assert row["counts"]["event_objects"] == row["counts"]["objects"] == 2
    assert not row["eligible"] and not report["all_eligible"]
    assert report["provider_reads_performed"] is False
    data = json.dumps(report)
    for private in (TOKEN, URL, "private-event", "private-case", "private-actor", "Private activity"):
        assert private not in data


@pytest.mark.parametrize(("statement", "code"), [
    ("UPDATE logs SET events=events+1 WHERE log_id=?", "event_metadata_count_difference"),
    ("UPDATE logs SET objects=objects+1 WHERE log_id=?", "object_metadata_count_difference"),
    ("UPDATE logs SET rejected_events=1 WHERE log_id=?", "rejected_events"),
    ("DELETE FROM event_source_keys WHERE log_id=?", "events_without_exact_source_key"),
    ("DELETE FROM events WHERE log_id=?", "keys_without_event"),
    ("DELETE FROM event_source_namespaces WHERE log_id=?", "keys_without_namespace"),
    ("DELETE FROM event_objects WHERE log_id=?", "events_without_exact_case_link"),
    ("DELETE FROM objects WHERE log_id=?", "dangling_object_links"),
    ("UPDATE objects SET object_type='other' WHERE log_id=?", "malformed_or_foreign_case_objects"),
    ("UPDATE events SET resource='raw-actor' WHERE log_id=?", "malformed_resource_pseudonyms"),
    ("UPDATE events SET attributes='{\"private\":\"value\"}' WHERE log_id=?", "unexpected_event_attributes"),
    ("UPDATE event_objects SET qualifier='other' WHERE log_id=?", "events_without_exact_case_link"),
    ("DELETE FROM logs WHERE log_id=?", "missing_log_metadata"),
])
def test_structural_violations(statement, code):
    p = run(profile())
    mutate(statement, p["log_id"])
    item = audit(p)
    assert item["status"] == "violations"
    assert item["violations"][code] > 0
    assert item["eligible"]


def test_hold_fences_dispatch_and_snapshot_until_clean_review():
    p = run(profile())
    damage(p)
    payload = select(p)
    plan = integrity.inspect(payload)
    result = integrity.apply(approve(payload, plan))
    held = current(p)
    assert held["revision"] == p["revision"]+1
    assert held["integrity_hold"]["violation_codes"] == ["event_metadata_count_difference"]
    assert not held["enabled"]
    assert held["log_id"] == p["log_id"] and held["checkpoint"] == p["checkpoint"]
    assert result["source_data_modified"] is False
    assert result["remote_publications_withdrawn"] is False
    with pytest.raises(rest.FeedError, match="integrity_hold"):
        rest.change(held["profile_id"], held["revision"], True)
    with pytest.raises(rest.FeedError, match="integrity_hold"):
        rest.schedule_pin(held["profile_id"])
    assert rest.run_scheduled(held["profile_id"], p["revision"])[0] == "paused:rest_profile_changed"
    with pytest.raises(ValueError):
        snapshot.prepare(held["profile_id"], p["config"]["initial_since"], p["checkpoint"])
    dirty_release = integrity.inspect(select(held), action="release")
    assert not dirty_release["all_eligible"]
    with pytest.raises(rest.FeedError, match="ineligible"):
        integrity.apply(approve(select(held), dirty_release), action="release")
    # Simulate externally reviewed repair of the intentionally corrupted metadata.
    mutate("UPDATE logs SET events=events-1 WHERE log_id=?", p["log_id"])
    release = integrity.inspect(select(held), action="release")
    assert release["all_eligible"]
    integrity.apply(approve(select(held), release), action="release")
    released = current(p)
    assert released["integrity_hold"] is None
    assert not released["enabled"] and released["revision"] == held["revision"]+1
    enabled = rest.change(released["profile_id"], released["revision"], True)
    assert enabled["enabled"] and rest.schedule_pin(enabled["profile_id"]) == enabled["revision"]


def test_atomic_multi_target_rollback_and_stale_review():
    p, q = run(profile("A")), run(profile("B"))
    damage(p)
    damage(q)
    payload = select(p, q)
    plan = integrity.inspect(payload)
    assert plan["all_eligible"]
    assert integrity.inspect(select(q, p))["plan_sha256"] == plan["plan_sha256"]
    def fail(_):
        raise RuntimeError("before commit")
    with pytest.raises(RuntimeError, match="before commit"):
        integrity.apply(approve(payload, plan), _transaction_hook=fail)
    assert current(p)["integrity_hold"] is None
    assert current(q)["integrity_hold"] is None
    mutate("UPDATE logs SET events=events+1 WHERE log_id=?", q["log_id"])
    with pytest.raises(rest.FeedError, match="stale"):
        integrity.apply(approve(payload, plan))
    assert current(p)["revision"] == p["revision"]
    fresh = integrity.inspect(payload)
    assert len(integrity.apply(approve(payload, fresh))["changed"]) == 2


def test_hold_invalidates_active_intent_and_late_receipt():
    p = run(profile())
    admitted, _, _ = rest._begin(p["profile_id"], p["revision"], rest._now())
    damage(p)
    plan = integrity.inspect(select(p))
    assert plan["targets"][0]["active_or_unreconciled_intent"]
    integrity.apply(approve(select(p), plan))
    rest._fail(admitted, rest.FeedError("late_failure"))
    rows = history.history(profile_id=p["profile_id"])["runs"]
    assert rows[0]["state"] == "invalidated"
    assert rows[0]["error_code"] == "retained_integrity_hold"


def test_bound_refusal_and_unconfigured_feed(monkeypatch):
    p = profile()
    assert audit(p)["status"] == "not_applicable"
    p = run(p)
    monkeypatch.setattr(integrity, "MAX_ROWS_PER_TABLE", 1)
    row = audit(p)
    assert row["status"] == "limit_exceeded"
    assert row["counts_are_lower_bounds"]
    assert not row["eligible"]


def test_mixed_selection_and_approval_refusals():
    p, q = run(profile("A")), run(profile("B"))
    damage(p)
    mixed = integrity.inspect(select(p, q))
    assert not mixed["all_eligible"]
    with pytest.raises(rest.FeedError, match="ineligible"):
        integrity.apply(approve(select(p, q), mixed))
    plan = integrity.inspect(select(p))
    for flag in ("confirm_exact_findings", "confirm_local_containment", "confirm_remote_coordination"):
        with pytest.raises(rest.FeedError, match="review_required"):
            integrity.apply({**approve(select(p), plan), flag: False})
    assert current(p)["integrity_hold"] is None


def test_dataset_deletion_clears_hold_without_reenabling():
    p = run(profile())
    damage(p)
    plan = integrity.inspect(select(p))
    integrity.apply(approve(select(p), plan))
    ws.delete_log(p["log_id"])
    item = current(p)
    assert item["integrity_hold"] is None
    assert not item["enabled"] and item["status"] == "dataset_deleted"
