"""Case filter: keep cases that do not carry a tag (and, as before, those that do)."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_case_tags as case_tags
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
    rows = ["case,activity,time"] + [f"c{i},{a},2024-01-0{1 + i}T0{j}:00:00Z" for i in range(6) for j, a in enumerate("AB")]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]
    case_tags.add(log, {"tag": "off-model", "case_ids": ["c0", "c1"]})
    case_tags.add(log, {"tag": "vip", "case_ids": ["c1", "c2"]})
    return log


def _cases(log, cohort):
    def run(conn):
        return {"ids": sorted(c for (c,) in conn.execute("SELECT DISTINCT case_id FROM ce").fetchall())}
    return workspace._query_filtered(log, None, cohort, run)["ids"]


def test_without_tag_and_with_tag():
    log = _log()
    assert _cases(log, {"without_tag": "off-model"}) == ["c2", "c3", "c4", "c5"]
    assert _cases(log, {"tag": "off-model"}) == ["c0", "c1"]
    assert _cases(log, {"tag": "vip", "without_tag": "off-model"}) == ["c2"]
    assert _cases(log, {"without_tag": "nobody-has-this"}) == ["c0", "c1", "c2", "c3", "c4", "c5"]
    assert cohorts.describe(cohorts.parse_cohort({"without_tag": " off-model "}))["without_tag"] == " off-model "
    with pytest.raises(EventLogWorkspaceError):
        cohorts.parse_cohort({"without_tag": 3})


def test_saved_filter_keeps_without_tag():
    log = _log()
    saved = filter_sets.save(log, {"name": "Fitting cases", "cohort": {"without_tag": "off-model"}})
    assert saved["cohort"] == {"without_tag": "off-model"}


def test_query_parameter(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_case_tags as case_tags
rows = ["case,activity,time"] + [f"c{i},{a},2024-01-0{1+i}T0{j}:00:00Z" for i in range(5) for j, a in enumerate("AB")]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
case_tags.add(log, {"tag": "late", "case_ids": ["c0", "c3"]})
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    base='/v1/event-logs/'+log+'/summary'
    assert client.get(base+'?without_tag=late',headers=owner).json()['cases']==3
    assert client.get(base+'?tag=late',headers=owner).json()['cases']==2
    assert client.get(base+'?without_tag='+'x'*61,headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
