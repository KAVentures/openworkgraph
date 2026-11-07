"""Owned loopback dashboard with the Privacy panel routes the real entry point registers."""
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
from server.local_auth import create_dashboard_session  # noqa: E402

(scratch / 'session.txt').write_text(create_dashboard_session(), encoding='utf-8')
