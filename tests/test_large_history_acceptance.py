from __future__ import annotations

"""Large-history acceptance for the observed-workflow explorer (roadmap #264 Stage 1).

The equivalence check always runs. The 300k-event acceptance seeds a real,
isolated SQLite history and times the authenticated explorer endpoints; it is
opt-in (``OWG_SCALE_ACCEPTANCE=1``) because seeding alone takes minutes.
"""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shared.core import browser_utils

ROOT = Path(__file__).resolve().parents[1]


def _reference_is_browser_app(app, custom_patterns=None):
    # Verbatim pre-memoization implementation, kept as the behavioral oracle.
    def norm(value):
        value = (value or "").strip().lower()
        if value.endswith(".exe"):
            value = value[:-4]
        return re.sub(r"\s+", " ", value)

    value = norm(app)
    if not value:
        return False
    patterns = list(browser_utils.DEFAULT_BROWSER_APP_PATTERNS)
    if custom_patterns:
        patterns.extend(custom_patterns)
    normalized = [norm(p) for p in patterns if p]
    if value in normalized:
        return True
    return any(p and (p in value or value in p) for p in normalized if len(p) >= 3)


@pytest.mark.parametrize("custom", [None, [], ["Acme  Portal", "", "x"], ("Internal Browser.EXE",)])
@pytest.mark.parametrize("app", [
    "", "   ", None, "Google Chrome", "google  chrome beta", "MSEDGE.EXE", "Firefox ESR",
    "Gmail", "Salesforce", "Finder", "Code", "ar", "edge", "Acme Portal", "acme portal 2",
    "internal browser", "Preview", "Microsoft Excel", "Slack",
])
def test_memoized_browser_detection_matches_reference(app, custom):
    assert browser_utils.is_browser_app(app, custom) is _reference_is_browser_app(app, custom)


def test_custom_patterns_from_one_shot_iterables_and_config_changes():
    assert browser_utils.is_browser_app("Acme Portal", (p for p in ["acme portal"]))
    assert not browser_utils.is_browser_app("Acme Portal")
    # A changed admin config must not be answered from an earlier cached result.
    assert browser_utils.is_browser_app("Wombat", ["wombat"])
    assert not browser_utils.is_browser_app("Wombat", ["koala"])


SCALE_EVENTS = int(os.environ.get("OWG_SCALE_EVENTS", "300000"))

_SCALE_SCRIPT = textwrap.dedent(r"""
    import json, os, sys, tempfile, time
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    root = Path(tempfile.mkdtemp(prefix="owg-scale-"))
    os.environ.update(WORKFLOW_OBSERVER_DATA=str(root / "data"),
                      WORKFLOW_OBSERVER_AUTH_DIR=str(root / "auth"),
                      WORKFLOW_OBSERVER_CONFIG=str(root / "config.json"))
    sys.path.insert(0, os.getcwd())
    from fastapi.testclient import TestClient
    from server import db
    from server.secure_app import app
    from server import local_auth

    total = int(sys.argv[1])
    # Start once on the empty store like a real install, so one-time legacy
    # privacy migrations are recorded as complete before history accumulates.
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    # Five interleaved apps across 60 days of sessions: enough repetition for
    # candidate groups, enough rows to exceed every default source bound.
    apps = [("Gmail", "Inbox"), ("Salesforce", "Opportunity"), ("Google Sheets", "Q3 tracker"),
            ("Slack", "#ops"), ("Jira", "OPS board")]
    start = datetime.now(timezone.utc) - timedelta(days=60)
    batch = []
    for i in range(total):
        app_name, title = apps[(i // 3 + i // 997) % len(apps)]
        batch.append({
            "event_id": f"scale-{i:07d}", "session_id": f"s{i // 400}",
            "observed_at": (start + timedelta(seconds=i * 17)).isoformat().replace("+00:00", "Z"),
            "schema_version": "1", "actor_id": "human", "device_id": "device", "sensor_id": "sensor",
            "source": "desktop", "app": app_name, "window_title": title,
            "event_type": ("focus_span", "screen_click", "screen_scroll")[i % 3],
            "duration_seconds": 12, "metadata": {},
        })
        if len(batch) == 5000:
            db.insert_events(batch)
            batch = []
    if batch:
        db.insert_events(batch)

    started = time.perf_counter()
    timings = {}
    with TestClient(app) as client:
        timings["restart"] = time.perf_counter() - started
        auth = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
        assert client.get("/health").status_code == 200
        assert client.get("/v1/workflow-evidence/families").status_code in (401, 403)
        for name, path in {
            "families": "/v1/workflow-evidence/families?limit=20&rank_by_navigation=true",
            "transition_maps": "/v1/workflow-evidence/families?include_transition_maps=true",
        }.items():
            began = time.perf_counter()
            response = client.get(path, headers=auth)
            timings[name] = time.perf_counter() - began
            assert response.status_code == 200, response.text[:500]
            body = response.json()
            assert body.get("candidate_clusters"), name
            # A bounded scan of a larger history must say so, never imply completeness.
            assert body.get("source_limit_reached") is True, name
        with db.connect() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(json.dumps({"rows": rows, **timings}))
""")


@pytest.mark.skipif(os.environ.get("OWG_SCALE_ACCEPTANCE") != "1",
                    reason="set OWG_SCALE_ACCEPTANCE=1 to seed and time a 300k-event history")
def test_explorer_endpoints_stay_bounded_on_300k_event_history():
    import json

    result = subprocess.run(
        [sys.executable, "-c", _SCALE_SCRIPT, str(SCALE_EVENTS)],
        cwd=ROOT, text=True, capture_output=True, timeout=1800,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    timings = json.loads(result.stdout.strip().splitlines()[-1])
    print(timings)
    assert timings["rows"] == SCALE_EVENTS
    # The launcher's first /health probe has a 60s deadline.
    assert timings["restart"] < 30, timings
    # Generous runner-speed margins; each endpoint scans a bounded source window.
    assert timings["families"] < 60, timings
    assert timings["transition_maps"] < 60, timings
