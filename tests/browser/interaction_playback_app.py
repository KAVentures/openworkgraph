"""Owned loopback dashboard seeded with one recorded visit for the interaction replay (#634)."""
from datetime import datetime, timedelta, timezone
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
scratch = Path(os.environ['OWG_BROWSER_TEST_DIR'])
os.environ.update(WORKFLOW_OBSERVER_DATA=str(scratch / 'data'), WORKFLOW_OBSERVER_AUTH_DIR=str(scratch / 'auth'),
                  WORKFLOW_OBSERVER_CONFIG=str(scratch / 'config.json'))
from server.secure_app import app  # noqa: E402
import server.basic_mode_routes  # noqa: E402,F401
import server.browser_signal_routes  # noqa: E402,F401
import server.main as owg_main  # noqa: E402
from server import capture_depth  # noqa: E402
from server.db import init_db  # noqa: E402
from server.local_auth import create_dashboard_session  # noqa: E402

PORT = 8794
# This fixture runs on its own port so it can run beside other browser tests.
owg_main.LOCAL_WEB_ORIGINS.update({f"http://{host}:{PORT}" for host in ("127.0.0.1", "localhost")})

init_db()
capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION})
page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/inbox", "title": "Inbox"}
start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=3)


def send(action, offset_s, metadata=None, target=None):
    owg_main.browser_event(owg_main.BrowserEvent(observed_at=(start + timedelta(seconds=offset_s)).isoformat(),
                                                 action=action, page=page, target=target or {}, metadata=metadata or {}))


send("pointer_trajectory", 2, {"points": [[k * 250, round(0.1 + k * 0.1, 3), round(0.2 + k * 0.05, 3)] for k in range(9)]})
send("control_hover_dwell", 4, {"duration_ms": 900, "ended_by": "activated"},
     {"tag": "button", "role": "menuitem", "label": "Refund for Ada Lovelace"})
send("click", 5, {}, {"tag": "button", "role": "menuitem", "label": "Refund for Ada Lovelace"})
send("pointer_trajectory", 8, {"points": [[0, 0.9, 0.8], [500, 0.85, 0.75]]})
(scratch / 'session.txt').write_text(create_dashboard_session(), encoding='utf-8')
