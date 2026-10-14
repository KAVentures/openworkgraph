"""Current-work fallback must respect explicit saved-history grants."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
from mcp_server import compact
calls = []
mode = "off"

def fake_get(path, params=None):
    if path == "/v1/workflow-trace":
        calls.append(params or {})
        if len(calls) >= 3:
            return {"rows": [{
                "event_id": "yesterday", "observed_at": "2026-10-12T12:00:00Z",
                "app": "Google Docs", "event_type": "focus_span",
            }]}
        return {"rows": []}
    if path == "/v1/history/ai-access":
        return {"access": {
            "mode": mode,
            "since": "2026-10-12T00:00:00Z" if mode == "selected_range" else None,
            "until": "2026-10-13T00:00:00Z" if mode == "selected_range" else None,
        }}
    if path == "/v1/context-pulse":
        return {"recent_evidence": []}
    if path == "/v1/tasks":
        return {"tasks": [], "patterns": []}
    if path == "/v1/semantic-activity":
        return {"events": []}
    raise AssertionError((path, params))

compact.secure_runtime.secure_get = fake_get
compact.core._begin = lambda _name: None
compact.core._finish = lambda _name, data: data

for mode in ("off", "selected_range", "all_saved"):
    calls.clear()
    result = compact.get_current_work_context()
    if mode == "off":
        assert len(calls) == 2, calls
        assert result["scope_used"] == "current"
        assert "History" in result["scope_hint"]
        assert not result["trace"]["rows"]
    else:
        assert len(calls) == 3, (mode, calls)
        assert calls[-1]["scope"] == "all"
        if mode == "selected_range":
            assert calls[-1]["since"] == "2026-10-12T00:00:00Z"
        assert result["scope_used"] == "authorized_saved_history"
        assert result["trace"]["rows"], result
'''

def test_current_work_history_fallback_requires_explicit_grant():
    # The compact MCP tool installs runtime wrappers, so exercise it in its
    # own process instead of mutating the singleton app during pytest collection.
    child = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=ROOT, text=True, capture_output=True, timeout=30,
    )
    assert child.returncode == 0, child.stderr
