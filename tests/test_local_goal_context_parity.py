"""Local get_goal_context stays behind the same history guard as canonical trace."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_goal_context_uses_authorized_raw_trace_without_new_access():
    code = r'''
from mcp_server import compact
from mcp.server.mcpserver.exceptions import ToolError
calls = []
compact.core._begin = lambda *_: None
compact.core._finish = lambda _name, result: result
def secure_get(path, params=None):
    calls.append((path, params))
    assert path == "/v1/workflow-trace"
    assert params["scope"] in ("current", "all")
    return {
        "rows": [
            {"observed_at": "2026-10-14T07:15:00Z",
             "event_id": "work-1", "source": "browser_extension",
             "session_id": "s1", "window_title": "Contract Medlink",
             "app": "Google Docs", "event_type": "browser_navigation"},
            {"observed_at": "2026-10-14T07:16:00Z",
             "event_id": "work-2", "source": "browser_extension",
             "session_id": "s1", "window_title": "Medlink contract amendment",
             "app": "Google Docs", "event_type": "browser_click"},
        ],
        "has_more": False,
    }
compact.secure_runtime.secure_get = secure_get
result = compact.get_goal_context(goal="Continue Medlink contract", scope="current")
assert result["status"] == "possible_matches", result
assert result["source"] == "authorized_local_canonical_workflow_trace"
assert result["history_access_not_bypassed"] is True
assert result["examples"][0]["anchor_event_id"] in {"work-1","work-2"}
assert calls[-1][1]["scope"] == "current"
all_result = compact.get_goal_context(
    goal="Continue Medlink contract",
    scope="all", since="2026-10-13T00:00:00Z",
)
assert all_result["scope_used"] == "all"
assert calls[-1][1]["since"] == "2026-10-13T00:00:00Z"
before = len(calls)
for bad in ("history", "week", "yesterday"):
    try:
        compact.get_goal_context(goal="Continue Medlink contract", scope=bad)
        raise AssertionError("invalid scope accepted")
    except ToolError:
        pass
assert len(calls) == before
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stderr


def test_goal_context_does_not_swallow_history_denials():
    code = r'''
from mcp_server import compact
from mcp.server.mcpserver.exceptions import ToolError
compact.core._begin = lambda *_: None
compact.secure_runtime.secure_get = lambda *_args, **_kw: (_ for _ in ()).throw(
    ToolError("Saved-history AI access is OFF.")
)
try:
    compact.get_goal_context(goal="Continue Medlink contract", scope="all")
    raise AssertionError("history denied but tool returned evidence")
except ToolError as exc:
    assert "OFF" in str(exc)
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stderr
