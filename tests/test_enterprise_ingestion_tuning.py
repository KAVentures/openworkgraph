"""Reviewed tuning across retained profiles with actual DuckDB transactions."""
from datetime import timedelta
import json

import httpx
import pytest

from server import enterprise_ingestion_tuning as tuning
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from gateway.connector_jobs import profile_digest
from shared.enterprise_connector_contract import PROVIDERS

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


def selection(p, **changes):
    return {"profile_id": p["profile_id"], "revision": p["revision"], **changes}


def approval(payload, plan):
    return {**payload, "plan_sha256": plan["plan_sha256"], "confirm_exact_changes": True,
            "confirm_dispatch_reauthorization": True, "confirm_workers_reconciled": True}


def private(p):
    with ws._LOCK:
        c = rest._connect()
        try:
            return rest._get(c, p["profile_id"])
        finally:
            c.close()


def mutate(p, **changes):
    with ws._LOCK:
        c = rest._connect()
        try:
            current = rest._get(c, p["profile_id"])
            rest._put(c, {**current, **changes})
        finally:
            c.close()


def run(p, records):
    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={"records": records, "next_cursor": None})), **kwargs)
    return rest.run(p["profile_id"], p["revision"], confirm_reads=True,
                    confirm_retention=True, client_factory=factory)


def event(p, key, at):
    return {"id": key, "case": "private-case", "step": "Review",
            "time": rest._iso(at), "changed": rest._iso(at), "actor": "private-actor"}


def test_atomic_multi_feed_tuning_retains_exact_state_and_fences_old_dispatch(monkeypatch):
    p, q = profile("A"), profile("B")
    anchor = rest._stamp(p["config"]["initial_since"])
    first_event = event(p, "private-event", anchor + timedelta(hours=23, minutes=30))
    p = run(p, [first_event])
    q = rest.change(q["profile_id"], q["revision"], False)
    before_p, before_q = private(p), private(q)
    payload = {"targets": [selection(q, page_size=20), selection(p, window_hours=12, overlap_minutes=120)]}
    def no_network(*args, **kwargs):
        raise AssertionError("tuning must not contact a provider")
    with monkeypatch.context() as m:
        m.setattr(httpx.Client, "stream", no_network)
        plan = tuning.preview(payload)
        assert private(p) == before_p and private(q) == before_q
        assert plan["provider_reads_performed"] is False
        assert plan["target_count"] == 2
        assert tuning.preview({"targets": list(reversed(payload["targets"]))})["plan_sha256"] == plan["plan_sha256"]
        result = tuning.apply(approval(payload, plan))
    assert result["changes_applied_this_call"] == 2
    new_p, new_q = private(p), private(q)
    for old, new in ((before_p, new_p), (before_q, new_q)):
        assert new["revision"] == old["revision"] + 1
        assert {k: v for k, v in old.items() if k not in ("config", "revision")} == {
            k: v for k, v in new.items() if k not in ("config", "revision")}
    assert new_p["config"]["window_hours"] == 12
    assert new_p["config"]["overlap_minutes"] == 120
    assert not new_q["enabled"] and new_q["config"]["page_size"] == 20
    assert rest.schedule_pin(p["profile_id"]) == p["revision"] + 1
    assert rest.run_scheduled(p["profile_id"], p["revision"])[0] == "paused:rest_profile_changed"
    public = next(x for x in rest.inventory()["profiles"] if x["profile_id"] == p["profile_id"])
    assert profile_digest(public) != profile_digest(p)
    assert ws.list_logs()["logs"][0]["events"] == 1
    replay = tuning.apply(approval(payload, plan))
    assert replay["replayed"] and replay["changes_applied_this_call"] == 0
    assert private(p) == new_p
    # The next normal window can use the new overlap without changing
    # existing event identities or duplicating the earlier retained event.
    second_event = event(p, "private-event-2", anchor + timedelta(hours=25))
    result = run(public, [first_event, second_event])
    assert result["events_added"] == 1 and result["duplicates_skipped"] == 1
    assert result["log_id"] == p["log_id"]
    assert rest._stamp(result["checkpoint"]) == anchor + timedelta(hours=36)
    assert ws.list_logs()["logs"][0]["events"] == 2
    assert len(tuning.inventory()["receipts"]) == 1


def test_transaction_failure_rolls_back_every_profile_and_receipt():
    p, q = profile("A"), profile("B")
    payload = {"targets": [selection(p, page_size=10), selection(q, page_size=20)]}
    plan = tuning.preview(payload)
    before = [private(p), private(q)]
    def fail(c):
        assert c.execute("SELECT count(*) FROM enterprise_ingestion_tuning_receipts").fetchone()[0] == 1
        raise RuntimeError("injected crash before commit")
    with pytest.raises(RuntimeError, match="injected"):
        tuning.apply(approval(payload, plan), _transaction_hook=fail)
    assert [private(p), private(q)] == before
    assert tuning.inventory()["receipts"] == []
    assert tuning.apply(approval(payload, plan))["changes_applied_this_call"] == 2


@pytest.mark.parametrize("kind", ["revision", "checkpoint", "receipt", "cooldown", "active", "expired", "credential"])
def test_any_stale_state_blocks_entire_batch(kind, monkeypatch):
    p, q = profile("A"), profile("B")
    payload = {"targets": [selection(p, page_size=10), selection(q, page_size=20)]}
    plan = tuning.preview(payload)
    before_p = private(p)
    if kind == "revision":
        rest.change(q["profile_id"], q["revision"], False)
    elif kind == "checkpoint":
        run(q, [])
    elif kind == "receipt":
        mutate(q, last_result={"outcome": "failed", "error": "fixture"})
    elif kind == "cooldown":
        mutate(q, retry_not_before=123456)
    elif kind in ("active", "expired"):
        mutate(q, run_id="a"*32, lease_until=rest.time.time()+(120 if kind=="active" else -120))
    elif kind == "credential":
        monkeypatch.setenv(ENV, "different-token-value")
    with pytest.raises(rest.FeedError):
        tuning.apply(approval(payload, plan))
    assert private(p) == before_p and tuning.inventory()["receipts"] == []


@pytest.mark.parametrize("field", ["confirm_exact_changes", "confirm_dispatch_reauthorization", "confirm_workers_reconciled"])
def test_separate_approvals(field):
    p = profile()
    payload = {"targets": [selection(p, page_size=10)]}
    plan = tuning.preview(payload)
    with pytest.raises(rest.FeedError, match="approvals_required"):
        tuning.apply({**approval(payload, plan), field: False})
    assert private(p)["revision"] == p["revision"]


@pytest.mark.parametrize("changes", [
    {"window_hours": 0}, {"window_hours": True}, {"window_hours": 1, "overlap_minutes": 60},
    {"page_size": 501}, {"ingestion_lag_minutes": 10}, {"lag_days": 1},
    {"page_size": 5}, {"name": "new"}, {"initial_since": "2026-01-01T00:00:00Z"},
    {"mapping": {}}, {"credential_env": "OWG_REST_TOKEN_OTHER"},
])
def test_refuse_unsupported_and_noop_changes(changes):
    p = profile()
    with pytest.raises(rest.FeedError):
        tuning.preview({"targets": [selection(p, **changes)]})


def test_duplicate_targets_and_receipt_replay_mismatch():
    p = profile()
    row = selection(p, page_size=10)
    with pytest.raises(rest.FeedError):
        tuning.preview({"targets": [row, row]})
    payload = {"targets": [row]}
    plan = tuning.preview(payload)
    tuning.apply(approval(payload, plan))
    with pytest.raises(rest.FeedError, match="receipt_request_mismatch"):
        tuning.apply(approval({"targets": [selection(p, page_size=11)]}, plan))


def test_dataset_deleted_cannot_be_reconfigured():
    p = profile()
    at = rest._stamp(p["config"]["initial_since"]) + timedelta(hours=1)
    p = run(p, [event(p, "x", at)])
    ws.delete_log(p["log_id"])
    public = rest.inventory()["profiles"][0]
    with pytest.raises(rest.FeedError, match="dataset_deleted"):
        tuning.preview({"targets": [selection(public, page_size=12)]})


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_all_provider_operational_contracts(provider):
    rules = tuning.supported(provider)
    cfg = {k: v["min"] for k, v in rules.items()}
    cfg["window_hours"] = 24
    p = {"provider": provider, "config": cfg}
    change = tuning.Selection(profile_id="rest_"+"1"*24, revision=1, window_hours=48)
    before, after, updated = tuning.proposed(p, change)
    assert before["window_hours"] == 24 and after["window_hours"] == 48
    assert updated["window_hours"] == 48
    if provider == "sap":
        assert updated["window_days"] == 2 and updated["overlap_days"] == 0
        with pytest.raises(rest.FeedError):
            tuning.proposed(p, tuning.Selection(profile_id="rest_"+"1"*24, revision=1, window_hours=25))
    if provider == "salesforce":
        with pytest.raises(rest.FeedError):
            tuning.proposed(p, tuning.Selection(profile_id="rest_"+"1"*24, revision=1, page_size=199))


def test_preview_time_is_not_a_state_pin_and_no_secret_in_receipts():
    p = profile()
    payload = {"targets": [selection(p, window_hours=48)]}
    now = rest._now()
    first = tuning.preview(payload, now=now)
    second = tuning.preview(payload, now=now+timedelta(seconds=30))
    assert first["previewed_at"] != second["previewed_at"]
    assert first["plan_sha256"] == second["plan_sha256"]
    result = tuning.apply(approval(payload, first), now=now+timedelta(seconds=30))
    data = json.dumps([first, second, result, tuning.inventory()])
    assert TOKEN not in data and private(p)["key"] not in data
    assert "credential_binding" not in data and "raw-case" not in data
