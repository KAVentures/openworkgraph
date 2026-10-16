"""No pre-enrollment upload without deliberate date-bound preview and consent."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sqlite3

import pytest
from connector import history_backfill as hb
from connector.state import SyncState


def _dates():
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=3)).isoformat(), (now - timedelta(days=2)).isoformat()


def _setup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    auth = tmp_path / "auth"
    data.mkdir(); auth.mkdir()
    state = SyncState(data / "gateway_sync_state.db")
    state.set_int("history_enrollment_boundary_event_id", 10)
    state.set("history_sync_mode", "from_enrollment_forward")
    state.set_int("last_local_event_id", 1234)
    settings = SimpleNamespace(
        enabled=True, url="https://company-gateway.example.test",
        verify_tls=True, batch_size=2, local_policy={},
    )
    ctx = dict(data_dir=data,auth_dir=auth,state=state,settings=settings,
               token="synthetic-device-token",boundary=10)
    monkeypatch.setenv("OWG_ENABLE_HISTORY_BACKFILL", "1")
    monkeypatch.setattr(hb, "_context", lambda _: ctx)
    monkeypatch.setattr(hb, "load_or_create_upload_privacy_key", lambda _: "a"*64)
    monkeypatch.setattr(hb, "_eligible",
        lambda config, context, since, until, policy: (
            [{"event_id":"historic-1"},{"event_id":"historic-2"},{"event_id":"historic-3"}],
            {"scanned":5,"eligible":3,"excluded":2,"boundary":10},
        ))
    return ctx


def _policy(client,url):
    assert url == "https://company-gateway.example.test"
    return {"share_excluded":False}


def test_off_by_default_even_if_legacy_local_history_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("OWG_ENABLE_HISTORY_BACKFILL", raising=False)
    with pytest.raises(PermissionError):
        hb._context(tmp_path / "config.json")


def test_history_preview_never_sends_upload_or_moves_forward_cursor(tmp_path, monkeypatch):
    ctx = _setup(tmp_path, monkeypatch)
    start,end = _dates()
    p = hb.preview(tmp_path/"config.json",start,end,policy_reader=_policy)
    assert p["eligible"]==3 and p["excluded"]==2
    assert p["no_upload_performed"] is True
    assert p["normal_sync_cursor_unchanged"] is True
    assert p["requires_explicit_confirmation"] is True
    assert ctx["state"].get_int("last_local_event_id")==1234
    assert ctx["state"].get("history_backfill_preview_token") != p["confirmation_token"]


def test_confirm_requires_exact_consent_range_and_replays_no_old_cursor(tmp_path, monkeypatch):
    ctx = _setup(tmp_path, monkeypatch)
    start,end = _dates()
    p = hb.preview(tmp_path/"config.json",start,end,policy_reader=_policy)
    sent=[]
    def sender(client,url,events):
        sent.append([e["event_id"] for e in events])
        return {e["event_id"] for e in events}, {}
    with pytest.raises(PermissionError):
        hb.confirm(tmp_path/"config.json",start,end,
            confirmation_token=p["confirmation_token"],explicit_confirmation="yes",
            policy_reader=_policy,batch_sender=sender)
    assert sent==[]
    with pytest.raises(PermissionError):
        hb.confirm(tmp_path/"config.json",start,(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
            confirmation_token=p["confirmation_token"],
            explicit_confirmation=hb._CONFIRMATION,
            policy_reader=_policy,batch_sender=sender)
    assert sent==[]
    accepted = hb.confirm(tmp_path/"config.json",start,end,
        confirmation_token=p["confirmation_token"], explicit_confirmation=hb._CONFIRMATION,
        policy_reader=_policy,batch_sender=sender)
    assert accepted["acknowledged"]==3 and accepted["rejected"]==0
    assert sent==[["historic-1","historic-2"],["historic-3"]]
    assert ctx["state"].get_int("last_local_event_id")==1234
    with pytest.raises(PermissionError):
        hb.confirm(tmp_path/"config.json",start,end,
            confirmation_token=p["confirmation_token"],explicit_confirmation=hb._CONFIRMATION,
            policy_reader=_policy,batch_sender=sender)
    assert len(sent)==2


def test_reviewed_payload_or_remote_policy_change_invalidates_confirmation(tmp_path,monkeypatch):
    ctx = _setup(tmp_path,monkeypatch)
    start,end = _dates()
    p=hb.preview(tmp_path/"config.json",start,end,policy_reader=_policy)
    monkeypatch.setattr(hb,"_eligible",
        lambda *_:([{"event_id":"CHANGED"}],{"scanned":1,"eligible":1,"excluded":0,"boundary":10}))
    sent=[]
    with pytest.raises(PermissionError,match="changed"):
        hb.confirm(tmp_path/"config.json",start,end,
            confirmation_token=p["confirmation_token"],explicit_confirmation=hb._CONFIRMATION,
            policy_reader=_policy,batch_sender=lambda *_:sent.append("upload"))
    assert sent == []


def test_pause_and_revocation_are_checked_after_preview_before_send(tmp_path,monkeypatch):
    ctx=_setup(tmp_path,monkeypatch)
    start,end=_dates()
    p=hb.preview(tmp_path/"config.json",start,end,policy_reader=_policy)
    ctx["state"].set_bool("sharing_paused",True)
    with pytest.raises(PermissionError):
        hb.confirm(tmp_path/"config.json",start,end,
            confirmation_token=p["confirmation_token"],explicit_confirmation=hb._CONFIRMATION,
            policy_reader=_policy)
    assert ctx["state"].get_int("last_local_event_id")==1234


def test_timezone_and_old_age_bounds_fail_closed():
    with pytest.raises(ValueError):
        hb._window("2026-10-01T00:00:00","2026-10-02T00:00:00")
    with pytest.raises(ValueError):
        hb._window("2026-01-01T00:00:00+00:00","2026-05-01T00:00:00+00:00")


def test_eligibility_excludes_paused_ids_and_uses_sqlite_utc_offsets(tmp_path,monkeypatch):
    data=tmp_path/"data";auth=tmp_path/"auth"
    data.mkdir();auth.mkdir()
    st=SyncState(data/"gateway_sync_state.db")
    st.add_skip_range(2,2,"paused")
    local_db=data/"workflow_observer.db"
    with sqlite3.connect(local_db) as conn:
        conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY,event_id TEXT,observed_at TEXT,event_type TEXT,metadata_json TEXT)")
        conn.executemany(
            "INSERT INTO events(id,event_id,observed_at,event_type,metadata_json) VALUES(?,?,?,?,?)",
            [(1,"one","2026-10-12T13:00:00+02:00","focus_span","{}"),
             (2,"two","2026-10-12T11:00:00+00:00","focus_span","{}"),
             (3,"three","2026-10-12T10:00:00+00:00","focus_span","{}")])
    monkeypatch.setattr(hb,"local_owner_names",lambda _:[])
    monkeypatch.setattr(hb,"load_or_create_upload_privacy_key",lambda _: "a"*64)
    monkeypatch.setattr(hb,"prepare_event_for_gateway",lambda ev, policy:ev)
    monkeypatch.setattr(hb,"project_event_for_upload",lambda ev,**kw:ev)
    monkeypatch.setattr(hb,"read_discovery_state",lambda **kw:{"enabled":False})
    settings=SimpleNamespace(local_policy={})
    ctx=dict(data_dir=data,auth_dir=auth,settings=settings,state=st,boundary=2)
    rows,count=hb._eligible(tmp_path/"config.json",ctx,
                           "2026-10-12T11:00:00+00:00",
                           "2026-10-12T12:00:00+00:00",{})
    assert [x["event_id"] for x in rows] == ["one"]
    assert count["scanned"]==2 and count["excluded"]==1


def test_generic_ai_bearer_cannot_authorize_historical_upload(tmp_path):
    """Run the secure-app import in a fresh process to prevent route state leaks."""
    import os
    import subprocess
    import sys
    script = """
import asyncio
from fastapi import HTTPException
from starlette.requests import Request
from server.secure_app import gateway_history_preview, gateway_history_confirm
for fn in (gateway_history_preview, gateway_history_confirm):
    request = Request({"type": "http", "method": "POST", "path": "/",
                       "headers": [(b"authorization", b"Bearer ordinary-local-api-token")]})
    try:
        asyncio.run(fn(request))
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "dashboard session" in exc.detail
    else:
        raise AssertionError("generic bearer must not count as historical consent")
"""
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "fresh"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth")}
    result = subprocess.run([sys.executable, "-c", script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr[-1800:]


def test_real_device_privacy_projection_used_for_historical_rows(tmp_path, monkeypatch):
    import json
    data=tmp_path/"data";auth=tmp_path/"auth"
    data.mkdir();auth.mkdir()
    st=SyncState(data/"gateway_sync_state.db")
    db=data/"workflow_observer.db"
    event_metadata={
        "action":"click",
        "page":{"hostname":"mail.example.com","pathname":"/users/katarina/private",
                "preview":"Patient: Karl Gustafsson, 54 år"},
        "target":{"role":"button","label":"Svar till Michaela"},
        "typed_value":"Karl Gustafsson",
        "custom_context":"Katarina",
    }
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY,event_id TEXT,observed_at TEXT,event_type TEXT,source TEXT,app TEXT,window_title TEXT,metadata_json TEXT,session_id TEXT)")
        conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",(
            1,"historical-private-1","2026-10-12T08:00:00Z","browser_click",
            "browser_extension","Google Chrome","Gmail · Katarina kommenterade avtalet",
            json.dumps(event_metadata),"historical-private-session",
        ))
    monkeypatch.setattr(hb,"local_owner_names",lambda _config: [])
    monkeypatch.setattr(hb,"load_or_create_upload_privacy_key",lambda _auth: "a"*64)
    monkeypatch.setattr(hb,"read_discovery_state",lambda **kw:{"enabled":False})
    settings=SimpleNamespace(local_policy={"share_window_titles":True, "share_metadata":True})
    ctx=dict(data_dir=data,auth_dir=auth,settings=settings,state=st,boundary=1)
    rows,counters=hb._eligible(tmp_path/"config.json",ctx,
        "2026-10-12T00:00:00+00:00","2026-10-13T00:00:00+00:00",{})
    assert counters["eligible"]==1
    result=json.dumps(rows[0],ensure_ascii=False)
    # Privacy audit flags such as preview_text_dropped are safe, and SHOULD
    # remain visible. Forbid the actual raw "preview" field and private values.
    for private in ("Karl Gustafsson", "Katarina", "Michaela",
                    "/users/katarina", "typed_value", "custom_context"):
        assert private not in result
    assert '"preview":' not in result
    assert rows[0]["metadata"]["gateway_sync"]["preview_text_dropped"] is True
    assert rows[0]["metadata"]["page"]["hostname"]=="mail.example.com"
    assert rows[0]["metadata"]["gateway_sync"]["raw_metadata_preserved"] is False


def test_atomic_state_token_consumption_is_single_use_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    state = SyncState(tmp_path / "sync_state.db")
    state.set("once", "token-hash")
    state.set("companion", "sensitive-consent-digest")
    both_start = Barrier(2, timeout=10)

    def consume():
        both_start.wait()
        return state.consume_if_matches(
            "once", "token-hash", also_delete=("companion",)
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=15) for future in
                   (executor.submit(consume), executor.submit(consume))]
    assert sorted(results) == [False, True]
    assert state.get("once") == ""
    assert state.get("companion") == ""


def test_parallel_confirmations_cannot_reuse_same_preview(tmp_path, monkeypatch):
    """Two requests racing through a policy refresh must cause one upload only."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    ctx = _setup(tmp_path, monkeypatch)
    since, until = _dates()
    path = tmp_path / "config.json"
    preview = hb.preview(path, since, until, policy_reader=_policy)
    both_at_policy = Barrier(2, timeout=10)
    sent = []
    lock = Lock()

    def racing_policy(client, url):
        both_at_policy.wait()
        return _policy(client, url)

    def send(client, url, batch):
        with lock:
            sent.append([event["event_id"] for event in batch])
        return {event["event_id"] for event in batch}, {}

    def do_confirm():
        return hb.confirm(
            path, since, until,
            confirmation_token=preview["confirmation_token"],
            explicit_confirmation=hb._CONFIRMATION,
            policy_reader=racing_policy,
            batch_sender=send,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(do_confirm) for _ in range(2)]
        successes, denials = [], []
        for job in jobs:
            try:
                successes.append(job.result(timeout=20))
            except PermissionError as exc:
                denials.append(str(exc))
    assert len(successes) == 1 and len(denials) == 1
    assert successes[0]["acknowledged"] == 3
    assert sent == [["historic-1", "historic-2"], ["historic-3"]]
    assert ctx["state"].get_int("last_local_event_id") == 1234
