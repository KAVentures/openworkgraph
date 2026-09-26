from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from server.local_auth import ensure_api_token

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(url: str, process: subprocess.Popen, timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            if httpx.get(url, timeout=0.4).status_code < 500:
                return
        except Exception:
            time.sleep(0.08)
    raise AssertionError(f"process did not become ready: {url}")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=5)


@pytest.fixture
def v49_api(tmp_path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    data = tmp_path / "data"; auth = tmp_path / "auth"
    token = ensure_api_token(directory=auth)
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth),
        "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP": "v49-bootstrap",
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-18T15:00:00+00:00",
        "WORKFLOW_OBSERVER_API": base,
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.secure_app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    _wait(base + "/health", process)
    try:
        yield {"base": base, "env": env, "auth": auth, "token": token, "headers": {"Authorization": f"Bearer {token}"}}
    finally:
        _stop(process)


def _event(i: int, at: str, *, title: str | None = None, label: str = "") -> dict:
    return {
        "event_id": f"v49-{i}", "observed_at": at, "schema_version": "1.0",
        "organization_id": "org-internal", "actor_id": "actor-internal",
        "device_id": "device-internal", "sensor_id": "browser:test", "source": "browser_extension",
        "session_id": "session-v49", "app": "Fortnox",
        "window_title": title or f"Leverantörsfaktura 2026-{4400+i} – Northwind Industries AB",
        "event_type": "browser_click", "duration_seconds": 0.2, "screenshot_path": "/never/expose.png",
        "metadata": {
            "action": "click",
            "page": {"hostname": "fortnox.se", "pathname": "/invoices"},
            "target": {"role": "button", "label": label or f"Attestera faktura {i}"},
            "activity": {"keypress_count": i % 4, "click_count": 1, "engaged_seconds": 2.0},
        },
    }


def _insert(api, events: list[dict]) -> None:
    r = httpx.post(api["base"] + "/v1/events", json={"events": events}, headers=api["headers"], timeout=15)
    assert r.status_code == 200, r.text


def test_compact_trace_preserves_business_context_without_internal_fields(v49_api):
    same = "2026-09-18T16:00:00+00:00"
    _insert(v49_api, [_event(i, same if i < 4 else f"2026-09-18T16:00:{i:02d}+00:00") for i in range(12)])
    r = httpx.get(v49_api["base"] + "/v1/workflow-trace", params={"scope": "current", "limit": 5}, headers=v49_api["headers"])
    assert r.status_code == 200
    first = r.json()
    assert first["returned"] == 5 and first["has_more"] is True and first["next_cursor"]
    row = first["rows"][0]
    assert "Northwind Industries AB" in row["window_title"]
    assert "Attestera faktura" in row["target_label"]
    for forbidden in ("organization_id", "actor_id", "device_id", "sensor_id", "screenshot_path", "metadata_json", "schema_version"):
        assert forbidden not in row

    seen = [x["target_label"] for x in first["rows"]]
    cursor = first["next_cursor"]
    while cursor:
        page = httpx.get(v49_api["base"] + "/v1/workflow-trace", params={"cursor": cursor, "limit": 5}, headers=v49_api["headers"]).json()
        seen.extend(x["target_label"] for x in page["rows"])
        cursor = page["next_cursor"]
    assert len(seen) == 12
    assert len(set(seen)) == 12  # same-timestamp rows are neither skipped nor duplicated


def test_trace_snapshot_is_stable_while_new_events_arrive(v49_api):
    _insert(v49_api, [_event(i, f"2026-09-18T16:10:{i:02d}+00:00") for i in range(8)])
    first = httpx.get(v49_api["base"] + "/v1/workflow-trace", params={"limit": 3}, headers=v49_api["headers"]).json()
    snap = datetime.fromisoformat(first["snapshot_until"].replace("Z", "+00:00"))
    future = (snap + timedelta(seconds=2)).isoformat()
    _insert(v49_api, [_event(999, future, title="NEW AFTER SNAPSHOT", label="NEW AFTER SNAPSHOT")])
    labels = [x["target_label"] for x in first["rows"]]
    cursor = first["next_cursor"]
    while cursor:
        page = httpx.get(v49_api["base"] + "/v1/workflow-trace", params={"cursor": cursor, "limit": 3}, headers=v49_api["headers"]).json()
        labels.extend(x["target_label"] for x in page["rows"])
        cursor = page["next_cursor"]
    assert "NEW AFTER SNAPSHOT" not in labels
    assert len(labels) == 8


def test_default_trace_budget_is_compact_for_one_hundred_events(v49_api):
    start = datetime(2026, 9, 18, 16, 20, tzinfo=timezone.utc)
    events = [_event(i, (start + timedelta(seconds=i)).isoformat()) for i in range(100)]
    _insert(v49_api, events)
    payload = httpx.get(v49_api["base"] + "/v1/workflow-trace", params={"limit": 100}, headers=v49_api["headers"]).json()
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert payload["returned"] == 100
    assert len(encoded) < 120_000  # safely below the old multi-hundred-KB duplicated bundles


def test_ai_access_is_off_by_default_and_secure_runtime_checks_every_call(v49_api):
    state = httpx.get(v49_api["base"] + "/v1/ai-access", headers=v49_api["headers"]).json()
    assert state == {"enabled": False, "resets_on_restart": True}

    code = "from mcp_server.secure_runtime import authorize_tool; authorize_tool('get_workflow_trace')"
    denied = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=v49_api["env"], capture_output=True, text=True)
    assert denied.returncode != 0
    assert "AI access is OFF" in (denied.stderr + denied.stdout)
    activity = httpx.get(v49_api["base"] + "/v1/mcp-activity", headers=v49_api["headers"]).json()["items"]
    assert activity and activity[0]["tool"] == "get_workflow_trace" and activity[0]["status"] == "denied"

    enabled = httpx.post(v49_api["base"] + "/v1/ai-access", json={"enabled": True}, headers=v49_api["headers"])
    assert enabled.json()["enabled"] is True
    allowed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=v49_api["env"], capture_output=True, text=True)
    assert allowed.returncode == 0, allowed.stderr


def test_activity_log_records_shape_not_sensitive_search_arguments(v49_api):
    payload = {"tool": "search_work_history", "status": "ok", "rows": 18, "bytes": 4096, "range_start": "2026-09-18T15:00:00Z", "range_end": "2026-09-18T16:00:00Z", "query": "Secret Project Falcon"}
    r = httpx.post(v49_api["base"] + "/v1/mcp-activity", json=payload, headers=v49_api["headers"])
    assert r.status_code == 200
    item = r.json()
    assert item["tool"] == "search_work_history" and item["rows"] == 18
    assert "query" not in item and "Falcon" not in json.dumps(item)


def test_stdio_connection_config_contains_no_long_lived_mcp_bearer(v49_api):
    cfg = httpx.get(v49_api["base"] + "/v1/mcp-connection-config", headers=v49_api["headers"]).json()
    assert cfg["transport"] == "stdio"
    assert cfg["command"] == sys.executable
    assert cfg["args"] == ["-m", "mcp_server.compact_stdio"]
    assert "token" not in cfg
    assert "Authorization" not in json.dumps(cfg)


def test_http_mcp_is_off_by_default_and_avoids_occupied_8788(v49_api):
    initial = httpx.get(v49_api["base"] + "/v1/mcp-http", headers=v49_api["headers"]).json()
    assert initial["running"] is False and initial["endpoint"] is None

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            blocker.bind(("127.0.0.1", 8788)); blocker.listen(1)
        except OSError:
            pytest.skip("port 8788 is already occupied by the CI environment")
        started = httpx.post(v49_api["base"] + "/v1/mcp-http", json={"action": "start"}, headers=v49_api["headers"], timeout=15)
        assert started.status_code == 200, started.text
        data = started.json()
        assert data["running"] is True
        assert data["endpoint"] != "http://127.0.0.1:8788/mcp"
        port = int(data["endpoint"].split(":")[2].split("/")[0])
        assert port != 8788
        stopped = httpx.post(v49_api["base"] + "/v1/mcp-http", json={"action": "stop"}, headers=v49_api["headers"])
        assert stopped.json()["running"] is False
    finally:
        blocker.close()


def test_mcpb_manifest_and_builder_are_local_compact_stdio_wrapper():
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == "0.3"
    assert manifest["server"]["type"] == "node"
    assert manifest["server"]["entry_point"] == "server/index.js"
    assert any(x.get("name") == "get_workflow_trace" for x in manifest["tools"])
    launcher = (ROOT / "mcpb" / "server" / "index.js").read_text(encoding="utf-8")
    assert "mcp_server.compact_stdio" in launcher
    assert "WORKFLOW_OBSERVER_API" in launcher
