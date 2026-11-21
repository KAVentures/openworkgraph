"""Case filter: keep cases that never do an activity (the opposite of the activity filter)."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_cohorts as cohorts
from server import event_log_filter_sets as filter_sets
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    """c0-c1 escalate, c1-c2 are reworked (B twice), c3-c5 are plain A > B."""
    paths = {0: "AEB", 1: "AEBB", 2: "ABB", 3: "AB", 4: "AB", 5: "AB"}
    rows = ["case,activity,time"] + [f"c{i},{a},2024-01-0{1 + i}T0{j}:00:00Z" for i, p in paths.items() for j, a in enumerate(p)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="w", source_format="csv", mapping=MAPPING)["log_id"]


def _cases(log, cohort):
    def run(conn):
        return {"ids": sorted(c for (c,) in conn.execute("SELECT DISTINCT case_id FROM ce").fetchall())}
    return workspace._query_filtered(log, None, cohort, run)["ids"]


def test_without_activity_alone_and_combined():
    log = _log()
    assert _cases(log, {"without_activity": "E"}) == ["c2", "c3", "c4", "c5"]
    assert _cases(log, {"activity": "E"}) == ["c0", "c1"]
    assert _cases(log, {"without_activity": "E", "rework": True}) == ["c2"]
    assert _cases(log, {"activity": "A", "without_activity": "E"}) == ["c2", "c3", "c4", "c5"]
    assert _cases(log, {"without_activity": "A"}) == []
    assert _cases(log, {"without_activity": "Nope"}) == ["c0", "c1", "c2", "c3", "c4", "c5"]
    assert cohorts.describe(cohorts.parse_cohort({"without_activity": "E"}))["without_activity"] == "E"
    for bad in (3, "x" * 201):
        with pytest.raises(EventLogWorkspaceError):
            cohorts.parse_cohort({"without_activity": bad})


def test_saved_filter_keeps_without_activity():
    log = _log()
    saved = filter_sets.save(log, {"name": "No escalation", "cohort": {"without_activity": "E"}})
    assert saved["cohort"] == {"without_activity": "E"}


def test_query_parameter(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},{a},2024-01-0{1+i}T0{j}:00:00Z" for i in range(5) for j, a in enumerate("AB" if i else "AXB")]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    base='/v1/event-logs/'+log
    assert client.get(base+'/summary?without_activity=X',headers=owner).json()['cases']==4
    assert client.get(base+'/variants?without_activity=X',headers=owner).json()['variants'][0]['path']==['A','B']
    assert client.get(base+'/summary?without_activity='+'x'*201,headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
