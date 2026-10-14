"""A Web run mirrored into agent evidence must appear only once."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_browser_projection_dedup_preserves_native_codex_and_other_tabs():
    script = r'''
from mcp_server import compact
compact.core._begin = lambda *_: None
compact.core._finish = lambda _name, result: result
def native(eid, framework, time):
    return {"execution_id":eid, "started_at":time,
            "agent":{"framework":framework,"name":"test"},
            "outcome_status":"unknown", "events":[]}
native_payload = {
    "executions": [
      native("execution:legacy-web", "chatgpt_web","2026-10-14T09:00:00Z"),
      native("execution:native-codex","codex","2026-10-14T09:01:00Z"),
      native("execution:other-web","chatgpt_web","2026-10-14T09:02:00Z"),
    ],
}
web_payload = {"executions":[
  {"execution_id":"execution:richer-web", "started_at":"2026-10-14T09:00:00+00:00",
   "source":"browser_ai_lifecycle", "provider":"chatgpt",
   "outcome_status":"unknown","resource_refs":["owg:r:"+"a"*32]},
]}
def get(path, params=None):
    if path == "/v1/agent-execution-traces":
        return native_payload
    if path == "/v1/browser-ai-runs":
        return web_payload
    raise AssertionError(path)
compact.secure_runtime.secure_get = get
result = compact.get_agent_runs()
ids = [row["execution_id"] for row in result["executions"]]
assert "execution:legacy-web" not in ids
assert ids == ["execution:other-web","execution:native-codex","execution:richer-web"], ids
assert result["duplicate_native_web_projections_suppressed"] == 1
assert result["executions"][-1]["resource_refs"]
web_payload["executions"] = []
legacy = compact.get_agent_runs()
assert any(row["execution_id"] == "execution:legacy-web" for row in legacy["executions"])
assert legacy["duplicate_native_web_projections_suppressed"] == 0
assert not compact._same_web_lifecycle_as_native(
  native("execution:z", "claude_code", "2026-10-14T09:00:00Z"),
  {"provider":"chatgpt","source":"browser_ai_lifecycle",
   "started_at":"2026-10-14T09:00:00Z"})
'''
    p = subprocess.run([sys.executable, "-c", script],
                       cwd=ROOT, capture_output=True, text=True, timeout=35)
    assert p.returncode == 0, p.stderr
