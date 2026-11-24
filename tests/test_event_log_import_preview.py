"""Bounded CSV preview shares the import parser without storing a dataset."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from server import event_log_import_preview as preview
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


def test_preview_uses_the_import_transform_and_reports_rejections(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "never-created")
    data = b"""case;activity;time;irrelevant
c1;Open;13/03/2024 09:05;secret
c1;Close;13/03/2024 10:05;unselected
;Open;13/03/2024 11:05;unselected
c2;Open;;unselected
"""
    result = preview.preview_csv(data, {**MAPPING, "delimiter": ";", "timestamp_format": "%d/%m/%Y %H:%M",
                                        "naive_timestamp_offset": "+01:00"})
    assert result["sample_rows"] == 4 and result["sample_limited"] is False
    assert result["accepted_events"] == 2 and result["distinct_cases"] == 1
    assert result["rejected_rows"] == 2
    assert result["timestamps_with_assumed_offset"] == 3
    assert result["rejected_by_reason"] == {"missing_case": 1, "missing_timestamp": 1}
    assert result["examples"][0] == {"case_id": "c1", "activity": "Open",
                                      "timestamp": "2024-03-13T08:05:00Z", "resource": ""}
    assert "secret" not in str(result)
    assert not workspace.DATA_DIR.exists()


def test_partial_sample_keeps_only_complete_records_within_both_limits(monkeypatch):
    complete = (b'case,activity,time,note\r\nc1,Open,2024-01-01T00:00:00Z,"multi\r\nline"\r\n'
                b'c2,Close,2024-01-01T01:00:00Z,second\r\n')
    for tail in (b'c3,Open,2024-01-01T02:00:00Z,"cut\r\ninside', b'c3,Open,2024-01-01T02:00:00Z,\xe2\x82'):
        result = preview.preview_csv(complete + tail, MAPPING, partial=True)
        assert result["sample_rows"] == 2 and result["rejected_rows"] == 0 and result["sample_limited"]
    monkeypatch.setattr(preview, "MAX_ROWS", 1)
    result = preview.preview_csv(complete, MAPPING)
    assert result["sample_rows"] == 1 and result["sample_limited"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="supported size"):
        preview.preview_csv(b"a" * (preview.MAX_BYTES + 1), MAPPING)


def test_preview_checks_source_ids_and_counts_exact_duplicates():
    data = b"""case,activity,time,event
c,Open,2024-01-01T00:00:00Z,e1
c,Open,2024-01-01T00:00:00Z,e1
"""
    mapping = {**MAPPING, "event_id_column": "event", "source_namespace": "crm"}
    result = preview.preview_csv(data, mapping)
    assert result["sample_rows"] == 2 and result["accepted_events"] == 1
    assert result["duplicate_source_ids_skipped"] == 1
    assert result["examples"][0]["case_id"] == "src:crm:c"
    with pytest.raises(workspace.EventLogWorkspaceError, match="conflict"):
        preview.preview_csv(data + b"c,Close,2024-01-01T00:00:00Z,e1\n", mapping)


def test_preview_route_requires_dashboard_session_and_streams_a_bounded_sample(tmp_path):
    script = r'''
from fastapi.testclient import TestClient
from server import local_auth
from server.secure_app import app
url = '/v1/event-logs/import/preview?case_column=case&activity_column=activity&timestamp_column=time'
sample = b'case,activity,time\nc,Open,2024-01-01T00:00:00Z'
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(url, content=sample, headers=bearer).status_code == 403
    response = client.post(url, content=sample, headers=owner)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store', response.text
    assert response.json()['accepted_events'] == 1
    assert client.get('/v1/event-logs', headers=owner).json()['logs'] == []
    assert client.post(url, content=b'x' * (512 * 1024 + 1), headers=owner).status_code == 413
    assert client.post(url.replace('case_column=case', 'case_column=unknown'), content=sample, headers=owner).status_code == 422
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth")}
    subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                   env=env, check=True, timeout=60)
