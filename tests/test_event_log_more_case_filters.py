"""Case filters for end time, events per case, path frequency (variation) and rework."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_cohorts as cohorts
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


# Five cases on A,B,C (ending Jan 1–5); two on A,B,B,C (rework, ending Feb 1–2); one on A,X (ending Mar 1).
PATHS = [("A", "B", "C")] * 5 + [("A", "B", "B", "C")] * 2 + [("A", "X")]
ENDS = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-02-01", "2024-02-02", "2024-03-01"]


def _import():
    rows = ["case,activity,time"]
    for i, (path, end) in enumerate(zip(PATHS, ENDS)):
        for j, activity in enumerate(path):
            rows.append(f"c{i},{activity},{end}T{j:02d}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="f", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _cases(log, cohort):
    def run(conn):
        return {"ids": sorted(c for (c,) in conn.execute("SELECT DISTINCT case_id FROM ce").fetchall())}
    return workspace._query_filtered(log, None, cohort, run)["ids"]


def test_each_new_filter():
    log = _import()
    assert _cases(log, {"ended_from": "2024-02-01T00:00:00Z"}) == ["c5", "c6", "c7"]
    assert _cases(log, {"ended_before": "2024-01-03T00:00:00Z"}) == ["c0", "c1"]
    assert _cases(log, {"events": {"min": 4}}) == ["c5", "c6"]
    assert _cases(log, {"events": {"max": 2}}) == ["c7"]
    assert _cases(log, {"events": {"min": 3, "max": 3}}) == ["c0", "c1", "c2", "c3", "c4"]
    assert _cases(log, {"rework": True}) == ["c5", "c6"]
    assert _cases(log, {"rework": False}) == ["c0", "c1", "c2", "c3", "c4", "c7"]
    # Variation: paths followed by at least 2 cases, and the rare ones followed by at most 2.
    assert _cases(log, {"path_cases": {"min": 2}}) == ["c0", "c1", "c2", "c3", "c4", "c5", "c6"]
    assert _cases(log, {"path_cases": {"max": 2}}) == ["c5", "c6", "c7"]
    assert _cases(log, {"path_cases": {"max": 1}}) == ["c7"]


def test_filters_combine_and_path_frequency_counts_all_cases():
    log = _import()
    # Path frequency is counted before the other filters, so c5 still has a path shared by 2 cases.
    assert _cases(log, {"ended_from": "2024-02-01T12:00:00Z", "path_cases": {"min": 2}}) == ["c6"]
    assert _cases(log, {"rework": False, "events": {"min": 3}, "ended_before": "2024-01-03T00:00:00Z"}) == ["c0", "c1"]
    described = cohorts.describe(cohorts.parse_cohort({"ended_from": "2024-02-01T00:00:00Z", "events": {"min": 2},
                                                       "path_cases": {"max": 3}, "rework": True}))
    assert described == {"ended_from": "2024-02-01T00:00:00Z", "events": {"min": 2}, "path_cases": {"max": 3},
                         "rework": True}


@pytest.mark.parametrize("bad", [
    {"events": {"min": 0}}, {"events": {"min": 2.5}}, {"events": {"min": True}}, {"events": {"x": 1}},
    {"events": {"min": 4, "max": 3}}, {"path_cases": {"max": -1}}, {"path_cases": 3}, {"rework": "yes"},
    {"ended_from": "2024-02-01T00:00:00Z", "ended_before": "2024-01-01T00:00:00Z"}, {"ended_from": "yesterday"},
])
def test_invalid_specs(bad):
    with pytest.raises(EventLogWorkspaceError):
        cohorts.parse_cohort(bad)


def test_query_parameters(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"]
paths = [("A","B","C")]*5 + [("A","B","B","C")]*2 + [("A","X")]
for i, p in enumerate(paths):
    for j, a in enumerate(p):
        rows.append(f"c{i},{a},2024-01-0{i+1}T0{j}:00:00Z")
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content="\n".join(rows).encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    base='/v1/event-logs/'+made.json()['log_id']+'/summary'
    def cases(q):
        r=client.get(base+q,headers=owner); assert r.status_code==200, r.text; return r.json()['cases']
    assert cases('')==8
    assert cases('?rework=true')==2 and cases('?rework=false')==6
    assert cases('?events_min=4')==2 and cases('?events_max=2')==1
    assert cases('?path_cases_max=1')==1 and cases('?path_cases_min=5')==5
    assert cases('?ended_from=2024-01-06T00:00:00Z')==3
    assert client.get(base+'?events_min=0',headers=owner).status_code==422
    assert client.get(base+'?ended_from=2024-02-01T00:00:00Z&ended_before=2024-01-01T00:00:00Z',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
