"""Root cause with a tagged outcome: which case features go with the cases carrying a tag."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_analytics as analytics
from server import event_log_case_tags as case_tags
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    """40 cases; the 12 tagged ones all go through Escalate (handled by bob), the rest rarely do."""
    rows = ["case,activity,time,who"]
    for i in range(40):
        escalate = i < 12 or i % 10 == 0
        steps = ["Open"] + (["Escalate"] if escalate else []) + ["Close"]
        for j, a in enumerate(steps):
            rows.append(f"c{i},{a},2024-01-{1 + i % 28:02d}T{8 + j:02d}:00:00Z,{'bob' if a == 'Escalate' else 'ana'}")
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="r", source_format="csv", mapping=MAPPING)["log_id"]
    case_tags.add(log, {"tag": "complaint", "case_ids": [f"c{i}" for i in range(12)]})
    return log


def test_tagged_outcome_finds_associated_features():
    log = _log()
    r = analytics.root_cause(log, "tagged", tag="complaint")
    assert r["outcome_cases"] == 12 and r["cases"] == 40 and r["outcome"]["tag"] == "complaint"
    top = r["associations"][0]
    assert top["feature"] in ({"kind": "activity", "value": "Escalate"}, {"kind": "resource", "value": "bob"})
    assert top["cases_with_feature"] == 14 and top["outcome_rate_without"] == 0.0
    assert top["risk_difference"] == round(12 / 14, 6) and top["holds_after_bonferroni"] is True


def test_tagged_outcome_validation_and_empty_tag():
    log = _log()
    for bad in (None, "", "x" * 61):
        with pytest.raises(EventLogWorkspaceError):
            analytics.root_cause(log, "tagged", tag=bad)
    nobody = analytics.root_cause(log, "tagged", tag="never-used")
    assert nobody["outcome_cases"] == 0 and nobody["associations"] == []


def test_route(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_case_tags as case_tags
rows = ["case,activity,time"] + [f"c{i},{a},2024-02-{1+i:02d}T0{j}:00:00Z" for i in range(20) for j, a in enumerate(["A"] + (["X"] if i < 8 else []) + ["B"])]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
case_tags.add(log, {"tag": "late", "case_ids": [f"c{i}" for i in range(8)]})
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    url='/v1/event-logs/'+log+'/root-cause?outcome=tagged&outcome_tag=late'
    assert client.get(url).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.json()['outcome_cases']==8, r.text
    assert client.get(url.replace('&outcome_tag=late',''),headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
