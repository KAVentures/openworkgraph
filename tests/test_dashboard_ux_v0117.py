from __future__ import annotations

"""v0.117 dashboard: human views stay human, one AI-access switch, Settings tab."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "c.json"), "PYTHONPATH": str(ROOT),
                "OWG_CONNECTIONS_HOME": str(tmp_path / "home"), "OWG_CLAUDE_SETTINGS_PATH": str(tmp_path / "home" / "settings.json")})
    env.pop("WORKFLOW_OBSERVER_RUN_STARTED_AT", None)
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=240)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_work_profile_and_evidence_leave_agent_runs_out(tmp_path):
    _run(r'''
from datetime import datetime, timedelta, timezone
from server.db import init_db, insert_events
from server.agent_ingest import ingest_agent_payloads
from server.evidence_query import query_evidence
from server.work_profile_service import compute_work_profile
from shared.claude_code_adapter import claude_hook_to_agent_events
init_db()
base = datetime.now(timezone.utc) - timedelta(minutes=30)
human = [{"event_id": f"h{i}", "observed_at": (base + timedelta(minutes=i)).isoformat(), "device_id": "d", "session_id": "s",
          "app": "Google Chrome", "window_title": "ChatGPT", "event_type": "focus_span", "duration_seconds": 50,
          "metadata": {"source": "desktop", "activity": {"foreground_seconds": 50, "engaged_seconds": 40}}} for i in range(3)]
insert_events(human)
def hook(name, sec, **extra):
    return claude_hook_to_agent_events({"session_id": "agent-1", "prompt_id": "p", "hook_event_name": name, "cwd": "/tmp/p", **extra},
                                       observed_at=(base + timedelta(minutes=10, seconds=sec)).isoformat())
ingest_agent_payloads(hook("UserPromptSubmit", 1) + hook("PostToolUse", 2, tool_name="Bash", tool_use_id="a",
                      tool_input={"command": "pytest"}, tool_response={"stdout": "1 passed"}) + hook("Stop", 3))

profile = compute_work_profile(scope="all")
surfaces = {x["surface"] for x in profile["ai_tool_usage"]}
assert "ChatGPT" in surfaces, surfaces
assert not any("claude" in s.lower() for s in surfaces), surfaces  # the agent is not "AI-tool usage"
assert not any("claude" in str(x.get("surface", "")).lower() for x in profile["navigation_hunting_candidates"])

evidence = query_evidence(scope="all", since=None, limit=100)
assert evidence["items"] and all(x["source"] != "agent" for x in evidence["items"])
assert not any("claude" in f["surface"].lower() for f in evidence["surfaces"])
assert query_evidence(scope="all", since=None, limit=100, include_agents=True)["total"] > evidence["total"]
''', tmp_path)


def test_playbooks_follow_the_agents_tab_hiding_choice():
    # Imported directly (not via the routes module, which would install the
    # authenticated app into this test process).
    from server.playbooks import without_hidden_frameworks

    families = [
        {"family_key": "a", "agent_frameworks": ["codex"]},
        {"family_key": "b", "agent_frameworks": ["claude-code"]},
        {"family_key": "c", "agent_frameworks": ["claude-code", "codex"]},
        {"family_key": "d", "agent_frameworks": []},
    ]
    # Only workflows run solely by agents whose Observe is off are left out.
    assert [f["family_key"] for f in without_hidden_frameworks(families, {"codex"})] == ["b", "c", "d"]
    assert [f["family_key"] for f in without_hidden_frameworks(families, set())] == ["a", "b", "c", "d"]
    routes = _read("server/playbook_routes.py")
    assert "hide_disconnected: bool = False" in routes and "without_hidden_frameworks(families, hidden)" in routes
    assert "call('/v1/playbooks/local'+agentHistoryParam())" in _read("dashboard/history_retention.js")


def test_dashboard_has_settings_tab_and_organization_is_only_organization():
    html = _read("dashboard/index.html")
    org = html.split('id="panel-organization"', 1)[1].split("</section>", 1)[0]
    settings = html.split('id="panel-settings"', 1)[1].split("</section>", 1)[0]
    assert "browserPairButton" in settings and "resetLearnedNames()" in settings
    assert "browserPairButton" not in org and "resetLearnedNames" not in org
    assert "activateTab('settings')" in html  # the browser-sensor chip goes to Settings
    assert "#panel-settings" in _read("dashboard/browser_signals.js")
    # Tab order: personal tabs first, organization last.
    order = re.findall(r'role="tab"[^>]+data-tab="([a-z]+)"', html)
    assert order[-2:] == ["settings", "organization"]


def test_one_ai_access_switch_and_no_stale_developer_text():
    secure = _read("server/secure_app.py")
    gateway = _read("dashboard/gateway_panel.js")
    html = _read("dashboard/index.html")
    # The old second toggle is gone; the card only shows reads.
    assert "aiAccessToggle" not in secure and "aiAccessToggle" not in gateway
    assert "Recent AI activity" in secure
    assert "AI access this run" in _read("dashboard/connections.js")
    assert "/v1/history/ai-access" in _read("dashboard/connections.js")
    for stale in ("v0.56.1", "Cursor paging is added", "stacked pattern", "stacked capture/timeline"):
        assert stale not in html, stale


def test_dashboard_polls_every_five_seconds_not_every_second():
    gateway = _read("dashboard/gateway_panel.js")
    loops = re.findall(r"setInterval\((.*?),(\d+)\);", gateway, flags=re.S)
    intervals = sorted(int(ms) for _, ms in loops)
    assert intervals == [1000, 5000], intervals
    one_second = next(body for body, ms in loops if ms == "1000")
    # The 1-second loop only ticks the recording clock locally: no requests.
    assert "tickClock" in one_second and "refresh" not in one_second and "fetch" not in one_second


def test_internal_identifiers_are_not_shown_as_labels():
    history = _read("dashboard/history_retention.js")
    assert "esc(f.family_key)" not in history and "esc(r.family_key" not in history
    profile = _read("dashboard/work_profile.js")
    assert "hunting candidates" not in profile.lower() and "candidate ·" not in profile
    # Single-switch settings save immediately; no checkbox + Save pairs.
    for removed in ("saveRunMemory", "saveOutcome", "saveEval"):
        assert removed not in history, removed
    assert 'role="switch"' in history
