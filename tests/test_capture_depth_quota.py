"""Storage quota for opt-in interaction detail (#634)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_storage_quota_trims_the_oldest_interaction_detail(tmp_path):
    code = r'''
from datetime import datetime, timedelta, timezone
from server import capture_depth, main, interaction_evidence
from server.db import init_db, connect
from server.main import BrowserEvent
init_db()
capture_depth.save({"profile": "full", "acknowledgement_version": capture_depth.FULL_ACK_VERSION,
                    "parameters": {"storage_mb": 10}})
capture_depth._MB = 2000  # a 20 kB quota keeps the test small
start = datetime.now(timezone.utc) - timedelta(hours=3)
page = {"origin": "https://work.example", "hostname": "work.example", "pathname": "/inbox", "title": "Inbox"}
points = [[k * 100, round(k / 200, 3), 0.5] for k in range(150)]
for i in range(20):
    main.browser_event(BrowserEvent(observed_at=(start + timedelta(minutes=i)).isoformat(), action="pointer_trajectory",
                                    page=page, metadata={"points": points}))
main.browser_event(BrowserEvent(observed_at=start.isoformat(), action="click", page=page, target={"tag": "button"}))
before = capture_depth.high_fidelity_storage()
assert before["events"] == 20 and before["bytes_estimate"] > before["limit_bytes"] == 20000, before
trim = capture_depth.enforce_storage_quota()
assert trim and trim["reason"] == "storage_quota" and trim["structural_evidence_kept"], trim
after = capture_depth.high_fidelity_storage()
assert after["bytes_estimate"] <= 0.9 * after["limit_bytes"], after
assert 0 < after["events"] < 20 and after["last_trim"]["deleted_events"] == trim["deleted_events"]
with connect() as conn:
    kept = [r[0] for r in conn.execute("SELECT observed_at FROM events WHERE event_type='browser_pointer_trajectory' ORDER BY observed_at").fetchall()]
    assert conn.execute("SELECT count(*) FROM events WHERE event_type='browser_click'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM pointer_trajectory_points").fetchone()[0] == len(kept)
# The newest are kept.
assert kept[-1] == max(kept) and len(kept) == after["events"]
assert capture_depth.enforce_storage_quota() is None
assert interaction_evidence.coverage_manifest()["high_fidelity_storage"]["events"] == after["events"]
try:
    capture_depth.save({"parameters": {"storage_mb": 5}}); raise SystemExit("5 MB accepted")
except capture_depth.CaptureDepthError:
    pass
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
