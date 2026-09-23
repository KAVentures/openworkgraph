from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from server import v0571_polish as polish

ROOT = Path(__file__).resolve().parents[1]


def _task(session: str, start: str, end: str) -> dict:
    return {
        "task_id": f"task-{session}",
        "session_id": session,
        "started_at": start,
        "ended_at": end,
        "surfaces": ["Gmail", "Salesforce", "Google Sheets", "Gmail"],
        "semantic_actions": ["Open email", "Open account", "Update status", "Send"],
        "engaged_seconds": 60.0,
        "elapsed_seconds": 90.0,
        "boundary": {"end_reason": "explicit_completion"},
        "outcomes": [],
        "anchor_event_ids": [],
        "task_family": "customer.followup",
        "completion_observed": True,
    }


def _factual(session: str, base_hour: int) -> list[dict]:
    surfaces = [
        ("Gmail", "Open email"),
        ("Salesforce", "Open account"),
        ("Google Sheets", "Update status"),
        ("Gmail", "Send"),
    ]
    rows = []
    for index, (surface, action) in enumerate(surfaces):
        minute = index * 2
        rows.append(
            {
                "session_id": session,
                "started_at": f"2026-09-23T{base_hour:02d}:{minute:02d}:00+00:00",
                "ended_at": f"2026-09-23T{base_hour:02d}:{minute + 1:02d}:00+00:00",
                "work_surface": surface,
                "semantic_actions": ["page_view", action],
                "evidence_event_ids": [f"{session}-{index}"],
            }
        )
    return rows


def test_pattern_steps_bind_actions_to_ordered_factual_surfaces():
    tasks = [
        _task("s1", "2026-09-23T08:00:00+00:00", "2026-09-23T08:07:00+00:00"),
        _task("s2", "2026-09-23T09:00:00+00:00", "2026-09-23T09:07:00+00:00"),
    ]
    derived = {
        "tasks": tasks,
        "patterns": [
            {
                "signature": "customer.followup",
                "task_family": "customer.followup",
                "observed_count": 2,
                "suggested_label": "Compose and send email",
                "surfaces": ["Gmail", "Salesforce", "Google Sheets", "Gmail"],
                # This is the exact v0.57 failure mode: a one-item skeleton must
                # never be zipped onto the first surface.
                "action_skeleton": ["send"],
                "confidence": "medium",
                "needs_review": True,
            }
        ],
        "inference": {},
    }
    factual = [*_factual("s1", 8), *_factual("s2", 9)]
    payload = polish._build_patterns_payload("all", derived, factual, None)
    pattern = payload["patterns"][0]
    assert pattern["steps"] == [
        {"surface": "Gmail", "action": "Open email"},
        {"surface": "Salesforce", "action": "Open account"},
        {"surface": "Google Sheets", "action": "Update status"},
        {"surface": "Gmail", "action": "Send"},
    ]
    assert pattern["steps"][0]["action"].lower() != "send"
    assert pattern["ends_with"] == "Send"
    assert pattern["step_source"] == "ordered_factual_context"
    assert payload["inference"]["surface_action_arrays_zipped_positionally"] is False


def test_pattern_steps_fall_back_to_surfaces_without_inventing_alignment():
    run = {
        "session_id": "none",
        "started_at": "2026-09-23T08:00:00+00:00",
        "ended_at": "2026-09-23T08:10:00+00:00",
        "surfaces": ["Gmail", "Salesforce", "Gmail"],
    }
    assert polish._run_steps(run, []) == [
        {"surface": "Gmail", "action": ""},
        {"surface": "Salesforce", "action": ""},
        {"surface": "Gmail", "action": ""},
    ]


def test_capture_markers_are_state_specific():
    paused = polish._capture_markers({"state": "paused", "state_changed_at": "2026-09-23T08:05:00+00:00"})
    assert paused["paused_at"] == "2026-09-23T08:05:00+00:00"
    assert paused["stopped_at"] is None
    stopped = polish._capture_markers({"state": "stopped", "state_changed_at": "2026-09-23T08:10:00+00:00"})
    assert stopped["paused_at"] is None
    assert stopped["stopped_at"] == "2026-09-23T08:10:00+00:00"


def test_sharing_policy_snapshot_uses_real_merge_logic_without_exposing_credentials(monkeypatch):
    settings = SimpleNamespace(
        enabled=False,
        url="",
        verify_tls=True,
        local_policy={
            "share_window_titles": False,
            "share_metadata": True,
            "allowed_event_types": ["focus_span", "browser_action"],
            "strip_metadata_keys": ["internal_note"],
        },
    )
    monkeypatch.setattr(polish, "load_gateway_settings", lambda *_a, **_k: settings)
    monkeypatch.setattr(polish, "load_device_token", lambda _settings: "")
    payload = polish.sharing_policy_snapshot()
    assert payload["connected"] is False
    assert payload["policy_current"] is True
    assert payload["effective_policy"]["share_window_titles"] is False
    assert payload["effective_policy"]["allowed_event_types"] == ["browser_action", "focus_span"]
    assert "typed_text" in payload["never_shared"]
    assert "token" not in payload


def test_new_policy_route_remains_behind_secure_dashboard_auth(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
import server.enterprise_app
import server.evidence_delete_routes
import server.v0571_polish
from server.secure_app import app
with TestClient(app) as client:
    response=client.get('/v1/sharing-policy')
    assert response.status_code==401, response.text
    script=client.get('/v0571-polish.js')
    assert script.status_code in {200,401}, script.text
'''
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
            "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
            "WORKFLOW_OBSERVER_MODE": "observe",
            "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-23T08:00:00+00:00",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_enterprise_runner_registers_correctness_layer():
    runner = (ROOT / "server" / "enterprise_runner.py").read_text(encoding="utf-8")
    assert "import server.v0571_polish" in runner
