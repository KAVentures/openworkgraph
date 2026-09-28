from __future__ import annotations

import json
from datetime import datetime, timezone

from server import agent_spool as spool
from server import connections
from shared.claude_code_adapter import claude_hook_to_agent_events


def _events() -> list[dict]:
    return claude_hook_to_agent_events(
        {
            "session_id": "s-observe",
            "prompt_id": "p-observe",
            "hook_event_name": "PostToolUse",
            "tool_use_id": "t-observe",
            "tool_name": "Read",
        },
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def test_observe_off_prevents_agent_event_from_being_spooled(tmp_path, monkeypatch):
    auth = tmp_path / "auth"
    data = tmp_path / "live"
    auth.mkdir()
    data.mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    (data / "capture_control.json").write_text(
        json.dumps({"state": "recording", "generation": 1, "skip_intervals": []}),
        encoding="utf-8",
    )
    spool.issue_lease(data, lease_id="observe-lease")

    connections._write_switch("claude_code", "observe", False)
    assert spool.spool_events(_events()) is False
    assert spool.pending_count() == 0

    connections._write_switch("claude_code", "observe", True)
    assert spool.spool_events(_events()) is True
    assert spool.pending_count() == 1
