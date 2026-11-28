"""Isolated OWG local dashboard fixture for scheduled-backup browser acceptance."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
scratch = Path(os.environ["OWG_BROWSER_TEST_DIR"])
os.environ.update(
    WORKFLOW_OBSERVER_DATA=str(scratch / "data"),
    WORKFLOW_OBSERVER_AUTH_DIR=str(scratch / "auth"),
    WORKFLOW_OBSERVER_CONFIG=str(scratch / "config.json"),
    OWG_BACKUP_DIR=str(scratch / "backups"),
)
from server.secure_app import app  # noqa: E402
import server.basic_mode_routes  # noqa: E402,F401
from server.local_scheduler_routes import router  # noqa: E402
from server.local_auth import create_dashboard_session  # noqa: E402
import server.main as owg_main  # noqa: E402

# The scheduled-backup (8791) and quiet-hours (8792) browser tests serve this fixture on their
# own ports so they can run beside other browser tests; the local-origin guard only knows the
# default 8787 dashboard, so allow those fixture origins too.
owg_main.LOCAL_WEB_ORIGINS.update({f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (8791, 8792)})

app.include_router(router)
(scratch / "session.txt").write_text(create_dashboard_session(), encoding="utf-8")


# Seed only this opt-in browser fixture. All retry times are in the future;
# no network transport or real source lookup is involved.
if os.environ.get("OWG_BROWSER_TEST_DELIVERY_PORTFOLIO") == "1":
    import hashlib
    import time
    from server import signal_notification_delivery as notices

    entries = {}
    for letter, digit in (("a", "1"), ("b", "2")):
        log_id = "log_" + letter * 16
        channel_id = "ndl_" + digit * 16
        rule_id = "sig_" + digit * 16
        state_fp = letter * 64
        state_key = hashlib.sha256((channel_id + rule_id + state_fp).encode("ascii")).hexdigest()
        state = {"rule_id": rule_id, "state_fingerprint": state_fp, "key": state_key}
        entry = notices._message([state], time.time())
        entry["next_attempt_epoch"] += 86400
        entries[log_id] = {"log_id": log_id, "channel_id": channel_id,
                           "receiver_url": "https://example.com/fixture-not-for-delivery",
                           "signing_secret": digit * 64, "enabled": True,
                           "created_at": notices._now(), "pending": [entry],
                           "seen": [], "dismissed": [], "audit": [], "delivered": 0,
                           "last_delivered_at": None, "consecutive_failures": 0,
                           "paused_reason": None}
    notices._save({"version": 1, "channels": entries})
