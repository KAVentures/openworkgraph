"""Ephemeral process-mining acceptance contracts and privacy regressions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.process_mining_csv import (
    MAX_BYTES, ProcessMiningCSVError, mine_csv_events,
)

ROOT = Path(__file__).resolve().parents[1]


def sample() -> bytes:
    return (
        "case_id,activity,timestamp,source\n"
        "secret-customer@example.com,Received,2026-10-20T10:00:00Z,CRM\n"
        "other-secret-customer,Received,2026-10-20T10:02:00Z,Helpdesk\n"
        "secret-customer@example.com,Checked,2026-10-20T10:05:00Z,CRM\n"
        "other-secret-customer,Approved,2026-10-20T12:06:00+02:00,Helpdesk\n"
        "secret-customer@example.com,Received,2026-10-20T10:08:00Z,CRM\n"
    ).encode()


def test_explicit_case_ids_separate_interleaved_rows_and_include_variants():
    result = mine_csv_events(sample())
    assert result["case_count"] == 2
    assert result["input_rows"] == 5
    assert result["activity_count"] == 3
    assert result["variant_count"] == 2
    assert result["metrics"]["repeated_activity_events"] == 1
    assert result["metrics"]["median_case_elapsed_seconds"] == 360.0
    assert result["metrics"]["median_inter_event_gap_seconds"] == 240.0
    assert result["metrics"]["inter_event_gaps_are_not_verified_waiting_time"]
    assert result["metrics"]["repeated_activities_are_not_verified_rework"]
    paths = {tuple(row["observed_path"]): row["case_count"] for row in result["variants"]}
    assert paths == {
        ("Received", "Checked", "Received"): 1,
        ("Received", "Approved"): 1,
    }
    edges = {(item["from"], item["to"]): item["transition_count"]
             for item in result["transitions"]}
    assert edges == {
        ("Received", "Checked"): 1,
        ("Checked", "Received"): 1,
        ("Received", "Approved"): 1,
    }
    assert all(not row["case_end_is_business_completion"] for row in result["cases"])
    assert result["business_outcomes_verified"] is False


def test_original_case_ids_and_source_values_are_never_returned():
    payload = json.dumps(mine_csv_events(sample()))
    assert "secret-customer@example.com" not in payload
    assert "other-secret-customer" not in payload
    assert "Helpdesk" not in payload
    assert "CRM" not in payload
    assert "Case 1" in payload and "Case 2" in payload
    assert '"import_retained": false' in payload
    assert '"agent_permissions_granted": false' in payload


@pytest.mark.parametrize("content", [
    b"",
    b"case_id,activity,timestamp,customer_email\ncase1,Open,2026-10-20T10:00:00Z,secret@example.com\n",
    b"case_id,activity,timestamp,case_id\nabc,Open,2026-10-20T10:00:00Z,def\n",
    b"case_id,activity,timestamp\nabc,Open,2026-10-20T10:00:00\n",
    b"case_id,activity,timestamp\nabc,<script>,2026-10-20T10:00:00Z\n",
    b"case_id,activity,timestamp\nabc,Open,not a timestamp\n",
    b"case_id,activity,timestamp\nabc,Open,2026-10-20T10:00:00Z,extra\n",
    b"case_id,activity,timestamp\nabc,Open\n",
    b"case_id,activity,timestamp\n",
    b"case_id,activity,timestamp\nabc,Open,2026-10-20T10:00:00Z\x00\n",
    b"\xff\xfe\x00",
])
def test_reject_malformed_unscoped_or_untrusted_files(content):
    with pytest.raises(ProcessMiningCSVError):
        mine_csv_events(content)


def test_csv_bounds_include_streaming_limit_and_actual_row_limit():
    with pytest.raises(ProcessMiningCSVError, match="1 MiB"):
        mine_csv_events(b"x" * (MAX_BYTES + 1))
    lines = ["case_id,activity,timestamp"] + [
        f"c{i // 30},Open,2026-10-20T10:00:00Z"
        for i in range(5_001)
    ]
    with pytest.raises(ProcessMiningCSVError, match="5,000"):
        mine_csv_events("\n".join(lines).encode())


def test_large_single_case_is_bounded_and_repeated_activities_counted():
    rows = ["case_id,activity,timestamp"] + [
        f"original-secret,Review,2026-10-20T10:{i // 60:02d}:{i % 60:02d}Z"
        for i in range(60)
    ]
    output = mine_csv_events("\n".join(rows).encode())
    assert output["input_rows"] == 60
    assert output["case_count"] == 1
    assert output["metrics"]["repeated_activity_events"] == 59
    assert output["variants"][0]["path_truncated"] is True
    assert len(output["variants"][0]["observed_path"]) == 24
    assert output["cases"][0]["path_truncated"] is True


def test_dashboard_route_and_no_default_upload_or_persistence():
    routes = (ROOT / "server/workflow_evidence_routes.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    script = (ROOT / "dashboard/process_mining.js").read_text(encoding="utf-8")
    secure = (ROOT / "server/secure_app.py").read_text(encoding="utf-8")
    assert '@app.post("/v1/process-mining/preview")' in routes
    assert 'async for chunk in request.stream()' in routes
    assert 'if len(payload) + len(chunk) > MAX_PROCESS_CSV_BYTES' in routes
    assert 'Cache-Control": "no-store"' in routes
    assert 'if path.startswith("/v1/"):' in secure
    assert 'if not _api_authenticated(request):' in secure
    assert '<script src="/process-mining.js"></script>' in dashboard
    assert "file.addEventListener('change'" in script
    assert "method:'POST'" in script
    assert "body:file" in script
    assert "window.__owgAuthReady" in script
    assert 'localStorage' not in script
    assert "indexedDB" not in script
    assert "sendBeacon" not in script
    assert "fetch('/v1/process-mining/preview'" in script
    assert "file.size>MAX_UPLOAD" in script


def test_real_authenticated_local_preview_stream_and_no_store(tmp_path):
    """Isolate middleware imports so existing in-process FastAPI tests remain unaltered."""
    import os
    import subprocess
    import sys

    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.process_mining_csv import MAX_BYTES

csv = b"case_id,activity,timestamp\ncase1,Received,2026-10-20T10:00:00Z\ncase1,Closed,2026-10-20T10:05:00Z\n"
with TestClient(app) as client:
    assert client.post("/v1/process-mining/preview", content=csv, headers={
        "Content-Type": "text/csv"
    }).status_code == 401
    auth = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session(),
            "Content-Type": "text/csv"}
    ok = client.post("/v1/process-mining/preview", content=csv, headers=auth)
    assert ok.status_code == 200, ok.text
    assert ok.headers.get("cache-control") == "no-store"
    assert ok.json()["case_count"] == 1
    assert ok.json()["variants"][0]["observed_path"] == ["Received", "Closed"]
    assert ok.json()["import_retained"] is False
    assert client.get("/process-mining.js").status_code == 200
    assert client.post("/v1/process-mining/preview", content=csv,
        headers={**auth, "Content-Type": "application/json"}).status_code == 415
    oversize = b"x" * (MAX_BYTES + 1)
    assert client.post("/v1/process-mining/preview", content=oversize,
        headers=auth).status_code == 413
    invalid = client.post("/v1/process-mining/preview",
        content=b"case_id,activity,timestamp\nc1,Open,2026-10-20T10:00:00\n",
        headers=auth)
    assert invalid.status_code == 422, invalid.text
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    })
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
