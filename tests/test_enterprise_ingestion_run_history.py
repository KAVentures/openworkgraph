"""Actual retained DuckDB journal/checkpoint transactions; synthetic provider."""
from datetime import timedelta
import json
import time

import httpx
import pytest

from server import enterprise_ingestion_run_history as history
from server import enterprise_rest_feed as rest
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


def event(key, at):
    return {"id": key, "case": "private-case", "step": "Private activity",
            "time": rest._iso(at), "changed": rest._iso(at), "actor": "private-actor"}


def run(p, records=(), *, response=None):
    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(lambda _: response if response is not None
            else httpx.Response(200, json={"records": list(records), "next_cursor": None})), **kwargs)
    return rest.run(p["profile_id"], p["revision"], confirm_reads=True,
                    confirm_retention=True, client_factory=factory)


def current(p):
    return next(x for x in rest.inventory()["profiles"] if x["profile_id"] == p["profile_id"])


def test_success_overlap_empty_windows_and_exact_summary():
    p = profile()
    start = rest._stamp(p["config"]["initial_since"])
    first_event = event("private-event-1", start + timedelta(hours=23, minutes=30))
    second_event = event("private-event-2", start + timedelta(hours=25))
    p = run(p, [first_event])
    p = run(p, [first_event, second_event])
    p = run(p)
    result = history.history(profile_id=p["profile_id"])
    rows = result["runs"]
    assert len(rows) == 3 and all(r["state"] == "succeeded" for r in rows)
    assert [r["events_added"] for r in rows] == [0, 1, 1]
    assert [r["duplicates_skipped"] for r in rows] == [0, 1, 0]
    assert rows[0]["checkpoint_after"] == p["checkpoint"]
    assert rows[0]["checkpoint_before"] == rows[1]["checkpoint_after"]
    assert rows[1]["checkpoint_before"] == rows[2]["checkpoint_after"]
    assert rows[2]["checkpoint_before"] is None
    assert result["summary"]["counts"]["events_added"]["sum_of_reported"] == 2
    assert result["summary"]["counts"]["query_bytes_billed"] == {
        "sum_of_reported": None, "reported_runs": 0, "unknown_successful_runs": 3}
    assert result["summary"]["states"]["succeeded"] == 3
    assert ws.list_logs()["logs"][0]["events"] == 2
    assert result["provider_reads_performed"] is False
    data = json.dumps(result)
    for secret in (TOKEN, "private-event", "private-case", "private-actor", "Private activity"):
        assert secret not in data


def test_failed_read_records_safe_error_and_cooldown_without_advancing():
    p = profile()
    with pytest.raises(rest.FeedError):
        run(p, response=httpx.Response(429, text="private provider payload",
                                      headers={"Retry-After": "120"}))
    failed = current(p)
    data = history.history(profile_id=p["profile_id"])
    row = data["runs"][0]
    assert row["state"] == "failed" and row["checkpoint_after"] is None
    assert row["events_added"] is None and row["retry_not_before"]
    assert row["error_code"] == failed["last_result"]["error"]
    assert failed["checkpoint"] is None and failed["log_id"] is None
    assert "private provider payload" not in json.dumps(data)
    # A refused cooldown dispatch is not an admitted attempt.
    with pytest.raises(rest.FeedError, match="cooldown"):
        run(failed)
    assert history.history(profile_id=p["profile_id"])["matching_retained_runs"] == 1


def test_expired_intent_passive_then_reviewed_replacement_preserves_distinction():
    p = profile()
    old, since, until = rest._begin(p["profile_id"], p["revision"], rest._now())
    data = history.history(state="running")
    assert len(data["runs"]) == 1
    future = old["lease_until"] + 1
    interrupted = history.history(state="interrupted", now=future)
    row = interrupted["runs"][0]
    assert row["stored_state"] == "running" and row["local_intent_unreconciled"]
    assert row["finished_at"] is None and row["remote_cancellation_verified"] is False
    with ws._LOCK:
        c = rest._connect()
        try:
            old["lease_until"] = time.time() - 1
            rest._put(c, old)
            c.execute("UPDATE enterprise_ingestion_runs SET lease_until=? WHERE run_id=?",
                      [old["lease_until"], old["run_id"]])
        finally:
            c.close()
    from server import enterprise_routine_reconciliation as recovery
    with pytest.raises(rest.FeedError,match="expired_routine_intent"):
        run(p)
    target={"profile_id":p["profile_id"],"revision":p["revision"],"run_id":old["run_id"]}
    plan=recovery.preview(target)
    receipt=recovery.apply({**target,"reviewed_at":plan["reviewed_at"],"plan_sha256":plan["plan_sha256"],
        "confirm_exact_intent":True,"confirm_workers_reconciled":True,
        "confirm_uncertain_remote_outcome":True,"confirm_disable_and_reauthorize":True})
    replacement = run(rest.change(p["profile_id"],receipt["revision"],True))
    records = history.history(profile_id=p["profile_id"])["runs"]
    assert len(records) == 2
    states = {r["state"]: r for r in records}
    assert states["interrupted"]["stored_state"] == "interrupted"
    assert not states["interrupted"]["local_intent_unreconciled"]
    assert states["interrupted"]["error_code"] == "expired_intent_reviewed_reconciliation"
    assert states["succeeded"]["checkpoint_after"] == replacement["checkpoint"]
    # A late result from the old attempt cannot replace the terminal receipt.
    rest._fail(old, rest.FeedError("late_failure"))
    assert history.history(profile_id=p["profile_id"])["runs"] == records


def test_disable_invalidates_admitted_run_and_late_failure_cannot_rewrite():
    p = profile()
    admitted, _, _ = rest._begin(p["profile_id"], p["revision"], rest._now())
    disabled = rest.change(p["profile_id"], p["revision"], False)
    assert not disabled["enabled"]
    rest._fail(admitted, rest.FeedError("late_error"))
    row = history.history(profile_id=p["profile_id"])["runs"][0]
    assert row["state"] == "invalidated"
    assert row["error_code"] == "profile_revision_changed"
    assert row["checkpoint_after"] is None


def test_retention_hook_failure_rolls_back_dataset_checkpoint_and_success_receipt(monkeypatch):
    p = profile()
    at = rest._stamp(p["config"]["initial_since"]) + timedelta(hours=1)
    original = history.finish
    def fail(c, profile_id, run_id, state, **kwargs):
        original(c, profile_id, run_id, state, **kwargs)
        if state == "succeeded":
            raise RuntimeError("injected ledger failure before commit")
    monkeypatch.setattr(history, "finish", fail)
    with pytest.raises(rest.FeedError):
        run(p, [event("private-event", at)])
    live = current(p)
    assert live["checkpoint"] is None and live["log_id"] is None
    assert ws.list_logs()["logs"] == []
    row = history.history(profile_id=p["profile_id"])["runs"][0]
    assert row["state"] == "failed" and row["checkpoint_after"] is None
    assert row["events_added"] is None


def test_empty_window_hook_failure_rolls_back_checkpoint(monkeypatch):
    p = profile()
    original = history.finish
    def fail(c, profile_id, run_id, state, **kwargs):
        original(c, profile_id, run_id, state, **kwargs)
        if state == "succeeded":
            raise RuntimeError("injected failure")
    monkeypatch.setattr(history, "finish", fail)
    with pytest.raises(rest.FeedError):
        run(p)
    assert current(p)["checkpoint"] is None
    assert history.history()["runs"][0]["state"] == "failed"


def test_admission_transaction_failure_does_not_install_lease(monkeypatch):
    p = profile()
    original = history.start
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("admission failure")
    monkeypatch.setattr(history, "start", fail)
    with pytest.raises(RuntimeError, match="admission"):
        rest._begin(p["profile_id"], p["revision"], rest._now())
    assert current(p)["status"] == "ready"
    assert history.history()["runs"] == []


def test_profile_removal_and_dataset_erasure_remove_associated_history():
    p, q = profile("A"), profile("B")
    at = rest._stamp(p["config"]["initial_since"]) + timedelta(hours=1)
    p = run(p, [event("private-event", at)])
    q = run(q)
    assert history.history()["returned_runs"] == 2
    ws.delete_log(p["log_id"])
    assert history.history(profile_id=p["profile_id"])["returned_runs"] == 0
    assert history.history(profile_id=q["profile_id"])["returned_runs"] == 1
    rest.delete(q["profile_id"], q["revision"])
    assert history.history()["returned_runs"] == 0
    with pytest.raises(rest.FeedError, match="unknown_rest_profile"):
        history.history(profile_id=q["profile_id"])


def test_filters_truncation_passivity_and_bounded_history(monkeypatch):
    p = profile()
    monkeypatch.setattr(history, "MAX_PER_PROFILE", 2)
    for _ in range(3):
        p = run(p)
    q = run(profile("other"))
    with monkeypatch.context() as m:
        m.setattr(httpx.Client, "stream", lambda *a, **k: pytest.fail("unexpected provider read"))
        m.setattr(rest, "_check_binding", lambda *a, **k: pytest.fail("unexpected credential check"))
        result = history.history(limit=1)
        assert result["truncated"] and result["matching_retained_runs"] == 3
        assert result["summary"]["runs"] == 1
        assert result["summary_scope"] == "returned_rows_only"
        assert history.history(profile_id=p["profile_id"])["returned_runs"] == 2
        assert history.history(profile_id=q["profile_id"])["returned_runs"] == 1
        assert history.history(state="failed")["returned_runs"] == 0


@pytest.mark.parametrize("kwargs", [
    {"days": 0}, {"days": 91}, {"days": True}, {"limit": 1001},
    {"state": "unknown"}, {"profile_id": "invalid"}, {"limit": False},
])
def test_invalid_filters(kwargs):
    with pytest.raises(rest.FeedError, match="invalid_ingestion_history_filter"):
        history.history(**kwargs)


def test_legacy_inflight_is_not_fabricated():
    p = profile()
    admitted, _, _ = rest._begin(p["profile_id"], p["revision"], rest._now())
    with ws._LOCK:
        c = rest._connect()
        try:
            c.execute("DELETE FROM enterprise_ingestion_runs")
        finally:
            c.close()
    rest._retain(admitted, [], {"pages": 1}, rest._stamp(admitted["config"]["initial_since"])+timedelta(days=1))
    assert current(p)["checkpoint"]
    assert history.history()["runs"] == []
