"""The real-log acceptance sweep runs end to end on a small synthetic log."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sweep_imports_calls_read_routes_and_reports(tmp_path):
    rows = ["case,activity,time,resource"]
    for c in range(30):
        for k, activity in enumerate(["Receive", "Check", "Approve" if c % 3 else "Reject", "Close"]):
            rows.append(f"c{c},{activity},2024-01-{1 + c % 28:02d}T{8 + k:02d}:00:00Z,r{c % 4}")
    log = tmp_path / "small.csv"
    log.write_text("\n".join(rows) + "\n")
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "real_log_acceptance.py"), str(log),
                           "--format", "csv", "--no-post", "--resource-column", "resource"], capture_output=True, text=True, timeout=600)
    report = json.loads(done.stdout)
    assert done.returncode == 0, (done.stderr[-2000:], report.get("server_errors"))
    assert report["import"]["status"] == 200 and report["import"]["events"] == 120
    assert report["summary"]["calls"] > 50 and report["summary"]["server_errors"] == 0
    assert {c["method"] for c in report["calls"]} == {"GET"}
    assert report["machine"]["cpus"] >= 1
