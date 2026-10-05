from __future__ import annotations

"""v0.118: Basic/Advanced dashboard, never-record lists, remembered AI access."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _run(code: str, tmp_path: Path) -> str:
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"), "PYTHONPATH": str(ROOT),
                "OWG_CONNECTIONS_HOME": str(tmp_path / "home"), "OWG_CLAUDE_SETTINGS_PATH": str(tmp_path / "home" / "settings.json")})
    # The server reads ROOT/config.json (as the launcher and recorder do); point
    # it at this test's file so the real checkout config is never touched.
    prelude = f"import server.main as _m; from pathlib import Path as _P; _m.CONFIG_PATH = _P({str(tmp_path / 'config.json')!r})\n"
    result = subprocess.run([sys.executable, "-c", prelude + code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=240)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout.strip()


def test_ai_access_is_on_on_a_new_install_and_explicit_off_is_remembered(tmp_path):
    check = "from server.ai_access import ai_access_enabled, resets_on_restart; print(ai_access_enabled(), resets_on_restart())"
    assert _run(check, tmp_path) == "True False"  # a new install is on at Redacted
    _run("from server.ai_access import set_ai_access; set_ai_access(True)", tmp_path)
    assert _run(check, tmp_path) == "True False"  # remembered after a "restart" (new process)
    state = tmp_path / "auth" / "ai_access.json"
    if os.name != "nt":
        assert oct(state.stat().st_mode & 0o777) == "0o600"
    # The older behavior is one setting away: off after every restart.
    _run("from server.ai_access import set_reset_on_restart; set_reset_on_restart(True)", tmp_path)
    assert _run(check, tmp_path) == "False True"
    _run("from server.ai_access import set_ai_access; set_ai_access(True)", tmp_path)
    assert _run(check, tmp_path) == "False True"
    # A corrupt state file never turns access on.
    state.write_text("{not json", encoding="utf-8")
    assert _run(check, tmp_path) == "False False"


def test_never_record_lists_are_cleaned_saved_and_keep_other_settings(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"window_title_mode": "full", "device_id": "keep-me"}), encoding="utf-8")
    out = _run(r'''
import json
from server import capture_exclusions as c
before = c.current()
assert before["apps"] == c.DEFAULTS["excluded_apps"] and before["hosts"] == []
after = c.update({"apps": ["Signal", " signal ", "Slack"], "hosts": ["https://MyBank.com/login", "mybank.com"], "title_words": ["medical"]})
assert after["apps"] == ["Signal", "Slack"], after
assert after["hosts"] == ["mybank.com"], after
assert after["title_words"] == ["medical"]
try:
    c.update({"apps": [f"app{i}" for i in range(101)]}); raise SystemExit("expected a limit")
except ValueError:
    pass
try:
    c.update({"apps": "Signal"}); raise SystemExit("expected a list")
except ValueError:
    pass
print(json.dumps(json.loads(open(c._config_path(), encoding="utf-8").read())))
''', tmp_path)
    saved = json.loads(out)
    assert saved["device_id"] == "keep-me" and saved["window_title_mode"] == "full"
    assert saved["excluded_apps"] == ["Signal", "Slack"]
    assert saved["excluded_browser_host_patterns"] == ["mybank.com"]


def test_never_record_applies_to_incoming_desktop_activity_without_a_recorder_restart(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"excluded_apps": ["Signal"], "excluded_title_patterns": ["medical"]}), encoding="utf-8")
    out = _run(r'''
from server.capture_exclusions import apply_to_desktop_event
from server.main import _runtime_config
cfg = _runtime_config()
def ev(app, title, **extra):
    return {"event_id": "e", "app": app, "window_title": title, "event_type": "screen_click",
            "metadata": {"source": "desktop", "target": {"label": "Send to Anna"}, "activity": {"engaged_seconds": 3}}, **extra}
hit = apply_to_desktop_event(ev("Signal", "Chat with Anna"), cfg)
assert hit["app"] == "Excluded" and hit["window_title"] == "" and "target" not in hit["metadata"], hit
assert hit["metadata"]["excluded"] is True and hit["metadata"]["activity"] == {"engaged_seconds": 3}
title_hit = apply_to_desktop_event(ev("Preview", "Medical results.pdf"), cfg)
assert title_hit["app"] == "Excluded"
miss = apply_to_desktop_event(ev("Mail", "Q4 pipeline"), cfg)
assert miss["app"] == "Mail" and miss["metadata"]["target"]["label"] == "Send to Anna"
agent = apply_to_desktop_event({"app": "Signal", "window_title": "x", "source": "agent", "event_type": "agent_tool_call", "metadata": {}}, cfg)
assert agent["app"] == "Signal"  # agent evidence has its own allowlists
browser = apply_to_desktop_event({"app": "Signal", "window_title": "x", "event_type": "browser_click", "metadata": {"source": "browser_extension"}}, cfg)
assert browser["app"] == "Signal"  # browser events go through the browser privacy path

# And through the real ingest route.
from server.db import init_db, connect
from server.main import EventBatch, ingest
init_db()
batch = EventBatch(events=[{"event_id": "x1", "observed_at": "2026-10-03T10:00:00+00:00", "device_id": "d", "session_id": "s",
                            "app": "Signal", "window_title": "Chat with Anna", "event_type": "focus_span", "duration_seconds": 30,
                            "metadata": {"source": "desktop"}}])
ingest(batch)
with connect() as conn:
    row = conn.execute("SELECT app, window_title FROM events WHERE event_id = 'x1'").fetchone()
print(row[0], repr(row[1]))
''', tmp_path)
    assert out.splitlines()[-1] == "Excluded ''"


def test_basic_mode_layer_is_installed_last_and_covers_the_privacy_controls():
    runner = _read("server/enterprise_runner.py")
    assert runner.index("import server.basic_mode_routes") > runner.index("import server.workflow_evidence_dashboard")
    assert runner.index("import server.basic_mode_routes") > runner.index("import server.dashboard_privacy")
    js = _read("dashboard/basic_mode.js")
    for endpoint in ("/v1/capture-exclusions", "/v1/history-policy", "/v1/run-memory/policy", "/v1/ai-access",
                     "/v1/history/ai-access", "/v1/ai-context", "/v1/browser-signal-settings", "/v1/evidence/delete"):
        assert endpoint in js, endpoint
    # Basic hides the power-user surfaces; nothing is deleted from the page.
    assert "for (const tab of ['history', 'export', 'settings']) mark(" in js
    for hidden in ("#discoveryModeCard", "#workProfileCard", "#agentSessionContinuity", "#owgAiDetail", "#gatewayStatusChip"):
        assert hidden in js, hidden
    assert "owg_view_mode_v1" in js and "'advanced'" in js


def test_first_run_privacy_fixes():
    html = _read("dashboard/index.html")
    assert '<input type="checkbox" id="includeRaw">' in html  # summary export by default
    assert 'id="redactNames" checked' in html
    polish = _read("dashboard/v0571_polish.js")
    assert "Nothing is shared: this computer is not connected to an organization" in polish
    secure = _read("server/secure_app.py")
    assert "response.status === 401 && !dashboardSession" in secure  # stale tab gets an explanation


def test_live_never_record_reloads_before_collector_local_persistence(tmp_path, monkeypatch):
    """A dashboard exclusion must take effect before JSONL/outbox/screenshots, not only at /v1/events."""
    from collector import main as collector
    from collector.interactions import RawInteraction
    from collector.outbox import EventOutbox

    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "excluded_apps": [],
        "excluded_title_patterns": [],
        "interaction_screenshots_enabled": True,
    }), encoding="utf-8")
    cfg = collector.load_config(config)
    cache: dict = {}

    visible = collector._live_public_window(config, cfg, cache, SimpleNamespace(app="Signal", title="Secret medical chat"))
    assert visible["excluded"] is False

    # This is what PUT /v1/capture-exclusions changes while the collector keeps
    # running. The next foreground/interaction snapshot must see it immediately.
    config.write_text(json.dumps({
        "excluded_apps": ["Signal"],
        "excluded_title_patterns": [],
        "interaction_screenshots_enabled": True,
    }), encoding="utf-8")
    excluded = collector._live_public_window(config, cfg, cache, SimpleNamespace(app="Signal", title="Secret medical chat"))
    assert excluded["excluded"] is True
    assert excluded["app"] == "Excluded" and excluded["window_title"] == ""

    screenshot_calls: list[str] = []
    monkeypatch.setattr(collector, "screenshot", lambda event_id: screenshot_calls.append(event_id) or "should-not-exist.jpg")
    event = collector._interaction_event(
        raw=RawInteraction(kind="click", x=1, y=2, occurred_mono=time.monotonic(), context=excluded),
        cfg=cfg,
        session_id="s-live-exclusion",
    )
    assert event["app"] == "Excluded" and event["window_title"] == ""
    assert event["screenshot_path"] is None
    assert screenshot_calls == []

    local_dir = tmp_path / "collector-local"
    local_dir.mkdir()
    monkeypatch.setattr(collector, "LOCAL_DIR", local_dir)
    outbox = EventOutbox(local_dir / "collector_outbox.db")
    collector.persist_event(event, outbox)

    jsonl = (local_dir / "events.jsonl").read_text(encoding="utf-8")
    queued = json.dumps(outbox.pending(10), ensure_ascii=False)
    assert "Secret medical chat" not in jsonl
    assert "Secret medical chat" not in queued
    assert '"app": "Excluded"' in jsonl
    assert outbox.pending(10)[0]["app"] == "Excluded"

    # Lock the actual runtime wiring too: foreground polling and interaction
    # snapshots both refresh exclusions before deriving public window state.
    source = _read("collector/main.py")
    assert "refresh_live_exclusions(config_path, cfg, live_exclusion_state)" in source
    assert "_live_public_window(config_path, cfg, live_exclusion_state)" in source


def test_managed_config_prefers_machine_policy_with_per_user_fallback():
    source = _read("server/org_join_routes.py")
    assert 'OWG_MANAGED_CONFIG' in source
    assert 'machine if machine.is_file() else user' in source
    assert 'Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "OpenWorkGraph" / "managed.json"' in source
    assert 'Path.home() / "Library" / "Application Support" / "OpenWorkGraph" / "managed.json"' in source


def test_update_check_exposes_direct_platform_installer_without_work_data():
    source = _read("server/update_check.py")
    assert "OpenWorkGraph-macOS.pkg" in source
    assert "OpenWorkGraph-Windows-Setup.exe" in source
    assert '"request_contains_work_evidence": False' in source
    assert '"installer_url": installer_url()' in source
