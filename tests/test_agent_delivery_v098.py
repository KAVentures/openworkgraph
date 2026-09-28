from __future__ import annotations

"""Agent delivery: lease-gated spool, telemetry diagnostics, hook refresh."""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapters import _agent_client
from server import agent_capture_runtime as runtime
from server import agent_spool as spool
from server import agent_telemetry_diagnostics as diagnostics
from shared.claude_code_adapter import claude_hook_to_agent_events


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    auth = tmp_path / "auth"
    data = tmp_path / "live"
    auth.mkdir()
    data.mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setattr(runtime, "_demo", lambda: False)
    runtime._STATE.update(lease_active=False, lease_valid_until=None, last_flush=None)
    return auth, data


def _state(data: Path, state: str) -> None:
    (data / "capture_control.json").write_text(json.dumps({"state": state, "generation": 1, "skip_intervals": []}), encoding="utf-8")


def _events(prompt: str = "p1", tool: str = "t1") -> list[dict]:
    return claude_hook_to_agent_events({
        "session_id": "s-delivery", "prompt_id": prompt, "hook_event_name": "PostToolUse",
        "tool_use_id": tool, "tool_name": "Read",
    }, observed_at=datetime.now(timezone.utc).isoformat())


# --- the lease -----------------------------------------------------------------------------------

def test_spooling_requires_a_live_recording_lease(dirs):
    _auth, data = dirs
    assert spool.spool_events(_events()) is False  # OpenWorkGraph never granted a lease
    _state(data, "recording")
    spool.issue_lease(data, lease_id="L1")
    assert spool.spool_events(_events()) is True
    _state(data, "paused")
    assert spool.spool_events(_events()) is False  # paused: dropped, not held for later
    _state(data, "stopped")
    assert spool.spool_events(_events()) is False
    _state(data, "recording")
    assert spool.spool_events(_events(), now=time.time() + spool.LEASE_TTL_SECONDS + 1) is False  # expired
    (data / "capture_control.json").unlink()
    assert spool.spool_events(_events()) is False  # unknown state is never "recording"
    spool.revoke_lease()
    assert spool.valid_lease() is None
    assert spool.pending_count() == 1


def test_runtime_grants_lease_only_while_recording_and_revokes_on_pause(dirs):
    _auth, data = dirs
    from shared.capture_control import initialize_run, set_state

    initialize_run("2026-09-28T08:00:00+00:00")
    runtime.tick()
    assert spool.valid_lease() is not None
    set_state("pause")
    runtime.tick()
    assert spool.valid_lease() is None
    set_state("resume")
    runtime.tick()
    assert spool.valid_lease() is not None
    runtime.stop()
    assert spool.valid_lease() is None


def test_demo_mode_never_grants_a_lease(dirs, monkeypatch):
    from shared.capture_control import initialize_run

    initialize_run("2026-09-28T08:00:00+00:00")
    monkeypatch.setattr(runtime, "_demo", lambda: True)
    runtime.tick()
    assert spool.valid_lease() is None


# --- flushing ------------------------------------------------------------------------------------

def test_flush_delivers_own_folder_skips_others_and_discards_old_or_invalid(dirs, tmp_path):
    _auth, data = dirs
    _state(data, "recording")
    spool.issue_lease(data, lease_id="L1")
    assert spool.spool_events(_events("p1"))
    # A file from another data folder (e.g. demo) and an old one.
    other = tmp_path / "demo"
    other.mkdir()
    _state(other, "recording")
    spool.issue_lease(other, lease_id="L2")
    assert spool.spool_events(_events("p3"))
    old = spool.spool_dir() / "0-old.json"
    old.write_text(json.dumps({"data_dir": str(data.resolve()), "spooled_epoch": time.time() - spool.MAX_AGE_SECONDS - 5, "events": _events("p4")}))
    bad = spool.spool_dir() / "1-bad.json"
    bad.write_text("{not json")

    delivered: list[list[dict]] = []
    counts = spool.flush_spool(data, delivered.append)
    assert counts == {"delivered_files": 1, "delivered_events": 1, "expired_files": 1, "rejected_files": 1}
    assert [e["run_id"] for batch in delivered for e in batch] == ["p1"]
    assert spool.pending_count() == 1  # the other folder's file is left for its owner


def test_flush_retries_later_when_storage_is_unavailable(dirs):
    _auth, data = dirs
    _state(data, "recording")
    spool.issue_lease(data, lease_id="L1")
    assert spool.spool_events(_events())

    def busy(_events):
        raise RuntimeError("database is locked")

    assert spool.flush_spool(data, busy)["delivered_files"] == 0
    assert spool.pending_count() == 1


def test_spooled_events_go_through_normal_ingest_rules(dirs):
    """Pause windows and the Observe switch still apply when spooled events arrive."""
    _auth, data = dirs
    from server.db import init_db, rows
    from shared.capture_control import initialize_run, set_state

    init_db()
    initialize_run("2026-09-28T08:00:00+00:00")
    runtime.tick()
    # Explicit times: on Windows the clock ticks ~15 ms, so "now" for the pause,
    # the event and the resume can coincide and leave an empty pause window.
    t0 = datetime.now(timezone.utc)
    at = lambda seconds: (t0 + timedelta(seconds=seconds)).isoformat()
    before = t0 - timedelta(seconds=60)
    kept = claude_hook_to_agent_events({
        "session_id": "s-delivery", "prompt_id": "kept-run", "hook_event_name": "PostToolUse", "tool_use_id": "k1", "tool_name": "Read",
    }, observed_at=at(-5))
    assert spool.spool_events(kept)
    set_state("pause", at=at(1))
    paused = claude_hook_to_agent_events({
        "session_id": "s-delivery", "prompt_id": "paused-run", "hook_event_name": "PostToolUse", "tool_use_id": "x", "tool_name": "Read",
    }, observed_at=at(2))
    # Written directly (as if spooled a moment before the pause took effect).
    (spool.spool_dir() / "9-late.json").write_text(json.dumps({"data_dir": str(data.resolve()), "spooled_epoch": time.time(), "events": paused}))
    set_state("resume", at=at(3))
    runtime._STATE["last_flush"] = None
    runtime.tick()
    assert spool.pending_count() == 0
    stored = rows("SELECT metadata_json FROM events WHERE source = 'agent' AND observed_at >= ?", (before.isoformat(),))
    runs = {r["metadata"]["trace"]["run_id"] for r in stored}
    assert "kept-run" in runs and "paused-run" not in runs


# --- the hook client -----------------------------------------------------------------------------

def test_client_spools_on_unreachable_openworkgraph_and_drops_without_lease(dirs, monkeypatch):
    _auth, data = dirs
    monkeypatch.setenv("WORKFLOW_OBSERVER_API", "http://127.0.0.1:9")  # nothing listens
    monkeypatch.setenv("OWG_AGENT_INGEST_TOKEN", "t")
    with pytest.raises(OSError):
        _agent_client.post_agent_events(_events())
    assert spool.pending_count() == 0
    _state(data, "recording")
    spool.issue_lease(data, lease_id="L1")
    assert _agent_client.post_agent_events(_events())["status"] == "spooled"
    assert spool.pending_count() == 1


def test_client_never_spools_a_rejection(dirs, monkeypatch):
    from urllib.error import HTTPError

    _auth, data = dirs
    _state(data, "recording")
    spool.issue_lease(data, lease_id="L1")

    def rejected(request, timeout):
        raise HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(_agent_client, "urlopen", rejected)
    monkeypatch.setenv("OWG_AGENT_INGEST_TOKEN", "t")
    with pytest.raises(HTTPError):
        _agent_client.post_agent_events(_events())
    assert spool.pending_count() == 0


# --- diagnostics ---------------------------------------------------------------------------------

@pytest.fixture()
def client():
    # Only the agent router: starting the full local app here would install its
    # middleware on the shared app object that later tests use.
    from fastapi import FastAPI

    from server.agent_routes import router

    app = FastAPI()
    app.include_router(router)
    diagnostics.reset_for_tests()
    with TestClient(app) as c:
        yield c


def test_diagnostics_locate_where_a_signal_stops(client):
    from server.agent_auth import ensure_agent_ingest_token
    from server.local_auth import ensure_api_token

    write = {"Authorization": "Bearer " + ensure_agent_ingest_token()}
    read = {"Authorization": "Bearer " + ensure_api_token()}
    hooks = {"events": _events("diag-run", "d1")}
    assert client.post("/agent-ingest/v1/events", json=hooks, headers={"Authorization": "Bearer wrong", "X-OWG-Channel": "claude_code_hooks"}).status_code == 401
    assert client.post("/agent-ingest/v1/events", json=hooks, headers=write).status_code == 200
    assert client.post("/agent-ingest/v1/claude-otel", json={"resourceLogs": []}, headers=write).status_code == 202
    assert client.post("/agent-ingest/v1/claude-otel", content=b"[1]", headers={**write, "Content-Type": "application/json"}).status_code == 422

    assert client.get("/v1/agent-telemetry/diagnostics", headers=write).status_code == 401  # write-only token cannot read
    report = client.get("/v1/agent-telemetry/diagnostics", headers=read).json()
    hooks_stats = report["channels"]["claude_code_hooks"]
    assert hooks_stats["requests"] == 2 and hooks_stats["rejected"] == {"auth": 1} and hooks_stats["events_stored"] >= 1
    otel = report["channels"]["claude_code_otel_logs"]
    assert otel["requests"] == 2 and otel["processed"] == 1 and otel["records_seen"] == 0 and otel["rejected"] == {"not_an_object": 1}
    assert report["channels"]["codex_otel"]["requests"] == 0 and report["channels"]["codex_otel"]["last_received_at"] is None
    assert "agent_spool" in report["configuration"] and "claude_code" in report["configuration"]
    # Only counts, timestamps and reason codes: nothing from the payloads.
    assert "diag-run" not in json.dumps(report) and "Read" not in json.dumps(report["channels"])


# --- Claude Code hook refresh --------------------------------------------------------------------

def _settings(tmp_path, monkeypatch, hooks: dict) -> Path:
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hooks": hooks, "env": {"MY_VAR": "1"}}), encoding="utf-8")
    monkeypatch.setenv("OWG_CLAUDE_SETTINGS_PATH", str(path))
    return path


def _owg_group():
    from adapters.claude_code_hook import settings_fragment

    return settings_fragment()["hooks"]["PostToolUse"]


OLD_EVENTS = ["SessionStart", "SessionEnd", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
              "PermissionDenied", "SubagentStart", "SubagentStop", "StopFailure"]


def test_outdated_owg_hooks_are_refreshed_and_user_hooks_kept(dirs, tmp_path, monkeypatch):
    user_hook = {"hooks": [{"type": "command", "command": "echo mine"}]}
    hooks = {event: _owg_group() for event in OLD_EVENTS}
    hooks["Stop"] = [user_hook]
    path = _settings(tmp_path, monkeypatch, hooks)
    from server import agent_config_writer as writer

    assert writer.claude_missing_hook_events(["Stop", "UserPromptSubmit", "SessionStart"]) == ["Stop", "UserPromptSubmit"]
    result = runtime.refresh_outdated_claude_hooks()
    assert result and result["added"] == ["Stop", "UserPromptSubmit"]
    data = json.loads(path.read_text())
    assert user_hook in data["hooks"]["Stop"] and len(data["hooks"]["Stop"]) == 2
    assert data["env"]["MY_VAR"] == "1"
    assert list(path.parent.glob("settings.json.owg-backup-*"))
    assert runtime.refresh_outdated_claude_hooks() is None  # now current


def test_hooks_are_never_installed_or_refreshed_without_consent(dirs, tmp_path, monkeypatch):
    path = _settings(tmp_path, monkeypatch, {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]})
    before = path.read_text()
    assert runtime.refresh_outdated_claude_hooks() is None  # OpenWorkGraph's hooks were never installed
    assert path.read_text() == before

    path.write_text(json.dumps({"hooks": {event: _owg_group() for event in OLD_EVENTS}}))
    from server import connections

    monkeypatch.setattr(connections, "is_enabled", lambda client, kind: False)  # Observe switched off
    before = path.read_text()
    assert runtime.refresh_outdated_claude_hooks() is None
    assert path.read_text() == before


def test_capture_status_reports_blind_sensors_and_away():
    from server.collector_status import sensor_state

    heartbeat = {"session_id": "s", "activity": {"permissions": {"accessibility": True, "input_monitoring": False}, "away": True}}
    assert sensor_state(heartbeat) == {"missing_permissions": ["input_monitoring"], "away": True}
    assert sensor_state({}) == {"missing_permissions": [], "away": False}
    assert sensor_state({"activity": {"permissions": {"accessibility": None}}})["missing_permissions"] == []  # unknown is not "missing"
    source = (Path(__file__).resolve().parents[1] / "server" / "enterprise_app.py").read_text()
    assert "value.update(sensor_state(main_module.COLLECTOR_STATUS))" in source


def test_ephemeral_history_survives_turns_and_is_purged_at_session_end(dirs):
    """A turn's Stop must not close the session in ephemeral mode (it used to purge
    the whole session and tombstone every later turn)."""
    from server.agent_ingest import ingest_agent_payloads
    from server.db import init_db, rows
    from shared.capture_control import initialize_run
    from shared.history_policy import update_retention

    init_db()
    initialize_run("2026-09-28T08:00:00+00:00")
    update_retention(human_mode="ephemeral", human_days=None, agent_mode="ephemeral", agent_days=None)

    def hook(name, prompt=None, **extra):
        payload = {"session_id": "eph-s", "hook_event_name": name, **extra}
        if prompt:
            payload["prompt_id"] = prompt
        return claude_hook_to_agent_events(payload, observed_at=datetime.now(timezone.utc).isoformat())

    def stored():
        return [r for r in rows("SELECT session_id FROM events WHERE source = 'agent'") if r["session_id"] == "eph-s"]

    ingest_agent_payloads(hook("SessionStart") + hook("UserPromptSubmit", "t1") + hook("Stop", "t1"))
    ingest_agent_payloads(hook("UserPromptSubmit", "t2") + hook("PostToolUse", "t2", tool_use_id="x", tool_name="Read"))
    ingest_agent_payloads(hook("SubagentStop", "t2", agent_id="a1", agent_type="explore"))
    assert len(stored()) == 6, "turns and subagents must not purge the running session"
    ingest_agent_payloads(hook("SessionEnd"))
    assert stored() == [], "the end of the session itself still purges ephemeral history"
