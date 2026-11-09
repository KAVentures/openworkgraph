"""Owned loopback dashboard seeded with focus spans for the focus report."""
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
import server.main as owg_main  # noqa: E402
from server import db  # noqa: E402
from server.local_auth import create_dashboard_session  # noqa: E402

PORT = 8796
owg_main.LOCAL_WEB_ORIGINS.update({f"http://{host}:{PORT}" for host in ("127.0.0.1", "localhost")})
db.init_db()
today = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)


def span(i, at, seconds, app):
    return {"event_id": f"f{i}", "session_id": "s", "observed_at": at.isoformat(), "schema_version": "1.0",
            "actor_id": "human", "device_id": "d", "sensor_id": "collector", "source": "collector", "app": app,
            "window_title": "", "event_type": "focus_span", "duration_seconds": seconds,
            "metadata": {"activity": {"engaged_seconds": seconds * 0.8, "idle_seconds": seconds * 0.2}}}


db.insert_events([span(1, today - timedelta(days=1), 1200, "Editor"), span(2, today - timedelta(days=1, seconds=-1200), 10, "Chat"),
                  span(3, today - timedelta(days=1, seconds=-1210), 1200, "Editor"), span(4, today - timedelta(days=1, seconds=-2410), 300, "Mail"),
                  span(5, today - timedelta(days=1, seconds=-2710), 300, "Editor")])
(scratch / 'session.txt').write_text(create_dashboard_session(), encoding='utf-8')
