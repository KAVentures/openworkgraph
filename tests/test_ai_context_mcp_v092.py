from __future__ import annotations

"""End-to-end: every Context MCP tool honours the AI context detail level.

Real API process + real compact stdio MCP server, as an AI client would use them.
"""

import asyncio
import json
import re
import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from server.local_auth import ensure_api_token

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Re: Contract for Anna Svensson - Gmail"
LABEL = "Open email from Anna Svensson"
# Context tools of the compact MCP server (experimental governance tools are off).
CONTEXT_TOOL_ARGS = {
    "get_current_work_context": {},
    "get_context_pulse": {},
    "list_history": {},
    "search_work": {"query": "Contract"},
    "get_workflow_trace": {"limit": 50},
    "get_work_profile": {},
    "find_repeated_workflows": {},
    "get_task_context": {},
    "how_did_similar_runs_go": {},
    "get_agent_runs": {},
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(url: str, process: subprocess.Popen) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            if httpx.get(url, timeout=0.4).status_code < 500:
                return
        except Exception:
            time.sleep(0.08)
    raise AssertionError("secure API did not start")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _text(result) -> str:
    parts = [getattr(item, "text", "") for item in result.content]
    if result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False))
    return "\n".join(parts)


def _events(now: datetime) -> list[dict]:
    events = []
    for day in range(3):
        base = now - timedelta(days=day, minutes=30)
        events.append({
            "event_id": f"ctx-title-{day}", "observed_at": base.isoformat(), "device_id": "d",
            "session_id": f"s{day}", "app": "Gmail", "window_title": TITLE,
            "event_type": "focus_span", "duration_seconds": 42.0, "metadata": {},
        })
        events.append({
            "event_id": f"ctx-click-{day}", "observed_at": (base + timedelta(seconds=5)).isoformat(),
            "device_id": "d", "session_id": f"s{day}", "app": "Gmail", "window_title": TITLE,
            "event_type": "ui_click", "duration_seconds": 0.2,
            "metadata": {"action": "click", "target": {"role": "button", "label": LABEL}},
        })
    return events


def test_every_context_mcp_tool_respects_detail_level(tmp_path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    data, auth, config = tmp_path / "data", tmp_path / "auth", tmp_path / "config.json"
    token = ensure_api_token(directory=auth)
    now = datetime.now(timezone.utc)
    run_started = (now - timedelta(days=4)).isoformat()
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth),
        "WORKFLOW_OBSERVER_CONFIG": str(config),
        "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP": "ai-context-test",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": run_started,
        "WORKFLOW_OBSERVER_API": base,
        "PYTHONPATH": str(ROOT),
    })
    api = subprocess.Popen(
        # The production entry point: secure app plus every additive route module.
        # Never leave an unread PIPE attached here: Windows anonymous pipe buffers
        # are small enough that uvicorn/access logs can fill them during the three
        # full MCP passes below, blocking the API child and masquerading as an MCP
        # HTTP timeout. This test never consumes those logs, so discard them.
        [sys.executable, "-m", "server.enterprise_runner", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        _wait(base + "/health", api)
        retention = httpx.put(
            base + "/v1/history-policy",
            json={"human_mode": "forever", "agent_mode": "forever", "onboarding_complete": True},
            headers=headers,
        )
        assert retention.status_code == 200, retention.text
        assert httpx.post(base + "/v1/events", json={"events": _events(now)}, headers=headers).status_code == 200
        assert httpx.post(base + "/v1/ai-access", json={"enabled": True}, headers=headers).json()["enabled"] is True
        lease = httpx.post(
            base + "/v1/history/ai-access",
            json={"mode": "all_saved", "expires_minutes": 60},
            headers=headers,
        )
        assert lease.status_code == 200 and lease.json()["access"]["mode"] == "all_saved"

        async def call_all() -> dict[str, tuple[str, dict]]:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mcp_server.compact_stdio"],
                cwd=str(ROOT),
                env={
                    "PYTHONPATH": str(ROOT),
                    "WORKFLOW_OBSERVER_API": base,
                    "WORKFLOW_OBSERVER_AUTH_DIR": str(auth),
                    "WORKFLOW_OBSERVER_DATA": str(data),
                    "WORKFLOW_OBSERVER_RUN_STARTED_AT": run_started,
                },
            )
            out: dict[str, tuple[str, dict]] = {}
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert set(CONTEXT_TOOL_ARGS) <= names, names
                    for name, args in CONTEXT_TOOL_ARGS.items():
                        result = await session.call_tool(name, args)
                        assert result.is_error is False, (name, _text(result)[:500])
                        out[name] = (_text(result), result.structured_content or {})
            return out

        # --- Redacted (default) ---
        redacted = asyncio.run(call_all())
        combined = "\n".join(text for text, _ in redacted.values())
        for name, (text, structured) in redacted.items():
            assert "Anna" not in text and "Svensson" not in text, (name, text[:800])
            assert structured.get("detail_level") == "redacted", (name, structured.get("detail_level"))
        assert "Open email from PERSON_" in combined
        assert "Re: Contract for PERSON_" in combined

        # --- Full (user choice) ---
        setting = httpx.post(base + "/v1/ai-context", json={"detail": "full"}, headers=headers)
        assert setting.status_code == 200 and setting.json()["detail_level"] == "full"
        full = asyncio.run(call_all())
        combined = "\n".join(text for text, _ in full.values())
        # Full = all stored context. Names were tokenized before storage (v0.108),
        # so Full keeps the whole title and label with the same person tokens.
        assert "Open email from PERSON_" in combined and "Re: Contract for PERSON_" in combined
        assert "Anna" not in combined and "Svensson" not in combined
        assert all(s.get("detail_level") == "full" for _, s in full.values())

        # An AI-context request can read but never change the setting.
        blocked = httpx.post(
            base + "/v1/ai-context", json={"detail": "full"},
            headers={**headers, "X-OpenWorkGraph-Context": "ai"},
        )
        assert blocked.status_code == 403

        # --- Organization policy forces Redacted over the user's Full ---
        cfg = json.loads(config.read_text(encoding="utf-8"))
        cfg["organization_ai_context_detail"] = "redacted"
        config.write_text(json.dumps(cfg), encoding="utf-8")
        state = httpx.get(base + "/v1/ai-context", headers=headers).json()
        assert state["user_setting"] == "full" and state["detail_level"] == "redacted"
        assert state["locked_by_organization"] is True
        forced = asyncio.run(call_all())
        combined = "\n".join(text for text, _ in forced.values())
        assert "Anna" not in combined and "Svensson" not in combined
        assert all(s.get("detail_level") == "redacted" for _, s in forced.values())
        refused = httpx.post(base + "/v1/ai-context", json={"detail": "full"}, headers=headers)
        assert refused.status_code == 409
    finally:
        _stop(api)

    # Stored evidence keeps the full title and label; only the name is a token.
    # This test explicitly chose forever retention above, so shutdown retention
    # cleanup must not remove it.
    db = sqlite3.connect(data / "workflow_observer.db")
    titles = {row[0] for row in db.execute("SELECT window_title FROM events")}
    labels = {json.loads(row[0] or "{}").get("target", {}).get("label") for row in db.execute("SELECT metadata_json FROM events")}
    assert len(titles) == 1 and re.fullmatch(r"Re: Contract for PERSON_[0-9A-F]{6} - Gmail", next(iter(titles))), titles
    assert any(re.fullmatch(r"Open email from PERSON_[0-9A-F]{6}", str(label)) for label in labels), labels
