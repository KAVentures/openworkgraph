"""R4: source-consistent one-file company handoff, no upload before explicit review."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from shared.analysis_handoff_bundle import HandoffBundle, FORMAT
from server import analysis_handoff_bundle as handoff
from server import event_log_workspace as workspace


@pytest.fixture
def local_log(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    return workspace.import_log(BytesIO(
        b"case,activity,time,resource\n"
        b"private-customer-1,Submit,2026-01-01T00:00:00Z,private-user-1\n"
        b"private-customer-1,Review,2026-01-01T00:01:00Z,private-user-2\n"
        b"private-customer-2,Submit,2026-01-02T00:00:00Z,private-user-3\n"),
        name="private-customer-export", source_format="csv", mapping={
            "case_column": "case", "activity_column": "activity",
            "timestamp_column": "time", "resource_column": "resource"})["log_id"]


def spec():
    return {"level": "cases", "dimensions": [],
            "measures": [{"fn": "count"}]}


def test_one_locked_handoff_is_minimized_exact_pair(local_log):
    result = handoff.prepare(local_log, spec())
    assert result["format"] == FORMAT
    assert result["aggregate_snapshot"]["matching_cases"] == len(result["case_dataset"]["cases"]) == 2
    assert result["aggregate_snapshot"]["source_content_sha256"] == result["case_dataset"]["source_content_sha256"]
    assert result["aggregate_snapshot"]["analysis_spec_sha256"] == result["case_dataset"]["analysis_spec_sha256"]
    assert result["source_independently_verified"] is False
    assert result["case_attachment_authorized"] is False
    assert result["organization_publication_authorized"] is False
    assert HandoffBundle.model_validate(result).model_dump(mode="json") == result
    serialized = json.dumps(result)
    for sensitive in ("private-customer-1", "private-customer-2", "private-user-1",
                      "private-user-2", "private-customer-export", local_log,
                      "2026-01-01", "case_id", "resource"):
        assert sensitive not in serialized
    assert result["case_dataset"]["cases"][0]["events"][1]["elapsed_microseconds"] == 60_000_000


@pytest.mark.parametrize("change", [
    lambda x: x["case_dataset"].update(source_content_sha256="a" * 64),
    lambda x: x["case_dataset"].update(analysis_spec_sha256="b" * 64),
    lambda x: x["aggregate_snapshot"].update(matching_cases=10),
    lambda x: x.update(case_attachment_authorized=True),
    lambda x: x.update(organization_publication_authorized=True),
    lambda x: x.update(source_independently_verified=True),
    lambda x: x.update(raw_events=[{"name": "private"}]),
    lambda x: x["case_dataset"].update(extra="secret"),
])
def test_bundles_with_wrong_source_or_authority_are_rejected(local_log, change):
    body = deepcopy(handoff.prepare(local_log, spec()))
    change(body)
    with pytest.raises(ValidationError):
        HandoffBundle.model_validate(body)


def test_ineligible_analysis_refuses_without_partial_packet(local_log):
    from server import event_log_workspace
    with pytest.raises(event_log_workspace.EventLogWorkspaceError):
        handoff.prepare(local_log, {"level": "events", "dimensions": [],
                                    "measures": [{"fn": "count"}]})


def test_local_http_requires_dashboard_and_explicit_preview_header(tmp_path):
    code = r"""
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
log=workspace.import_log(BytesIO(
    b'case,activity,time\na,A,2024-01-01T00:00:00Z\na,B,2024-01-01T00:01:00Z\n'),
    name='test',source_format='csv',
    mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id']
route=f'/v1/event-logs/{log}/company-handoff-preview'
spec={'level':'cases','dimensions':[],'measures':[{'fn':'count'}]}
with TestClient(app) as client:
    assert client.post(route,json=spec).status_code==401
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.post(route,json=spec,headers=bearer).status_code in (401,403)
    headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    assert client.post(route,json=spec,headers=headers).status_code==403
    headers['X-OWG-Company-Handoff-Preview']='true'
    response=client.post(route,json=spec,headers=headers)
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    result=response.json()
    assert result['format']=='openworkgraph.analysis-handoff-bundle.v1'
    assert len(result['case_dataset']['cases'])==result['aggregate_snapshot']['matching_cases']
    assert result['case_attachment_authorized'] is False
    assert result['organization_publication_authorized'] is False
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
