from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def test_rework_overlap_is_only_the_immediately_preceding_turn(tmp_path, monkeypatch):
    from shared import tool_detail
    from shared import workspace_rework as wr

    monkeypatch.setattr(wr, "enabled", lambda: True)
    monkeypatch.setattr(wr, "_state_path", lambda _workspace: tmp_path / "state.json")
    monkeypatch.setattr(tool_detail, "_file_ref_key", lambda: b"k")
    monkeypatch.setattr(tool_detail, "workspace_ref", lambda _cwd, key=None: "w:test")
    monkeypatch.setattr(tool_detail, "file_ref", lambda _path, key=None: "f:a")
    snapshots = iter([
        {"head": "h", "files": {"f:a": "agent-v1"}, "truncated": False},
        {"head": "h", "files": {"f:a": "person-v1"}, "truncated": False},
        {"head": "h", "files": {"f:a": "person-v1"}, "truncated": False},
        {"head": "h", "files": {"f:a": "person-v2"}, "truncated": False},
    ])
    monkeypatch.setattr(wr, "snapshot", lambda cwd, key: next(snapshots))

    base = {"cwd": str(tmp_path), "session_id": "s"}
    wr.process_claude_hook(
        {**base, "hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "a.py")}},
        [], now=1.0, lease_ok=True,
    )
    wr.process_claude_hook({**base, "hook_event_name": "Stop"}, [], now=2.0, lease_ok=True)
    first = [{"operation": "run_started"}]
    wr.process_claude_hook({**base, "hook_event_name": "UserPromptSubmit"}, first, now=3.0, lease_ok=True)
    assert first[0]["between_turns"]["agent_files_changed"] == 1

    # The second turn does not edit a.py. A later human change to a.py must not
    # be attributed to turn 2 merely because turn 1 edited it recently.
    wr.process_claude_hook({**base, "hook_event_name": "Stop"}, [], now=4.0, lease_ok=True)
    second = [{"operation": "run_started"}]
    wr.process_claude_hook({**base, "hook_event_name": "UserPromptSubmit"}, second, now=5.0, lease_ok=True)
    assert second[0]["between_turns"]["files_changed"] == 1
    assert second[0]["between_turns"]["agent_files_changed"] == 0


def test_workspace_state_lock_is_cross_process_on_every_platform(tmp_path, monkeypatch):
    from shared import workspace_rework as wr

    state = tmp_path / "state.json"
    script = r'''
import sys, time
from pathlib import Path
from shared.workspace_rework import _Locked
with _Locked(Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(0.8)
'''
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(state)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "locked"
        monkeypatch.setattr(wr, "LOCK_WAIT_SECONDS", 0.05)
        with pytest.raises(TimeoutError):
            with wr._Locked(state):
                pass
    finally:
        child.wait(timeout=10)
    assert child.returncode == 0, child.stderr.read() if child.stderr else ""
    with wr._Locked(state):
        assert state.with_suffix(".lock").exists()
    assert not state.with_suffix(".lock").exists()
