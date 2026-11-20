"""Follower rules with a time limit: keep cases where one step follows another within / after so many hours."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_cohorts as cohorts
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
# (case, [(activity, hour)]) — Submit then Approve after 2, 10, 30 or 60 hours; d0 approves directly,
# e0 has Check in between; n0 never approves; r0 approves twice (the first Approve counts) and Submit again.
CASES = {
    "a2": [("Submit", 0), ("Approve", 2)],
    "a10": [("Submit", 0), ("Approve", 10)],
    "a30": [("Submit", 0), ("Approve", 30)],
    "e60": [("Submit", 0), ("Check", 1), ("Approve", 60)],
    "n0": [("Submit", 0), ("Check", 5)],
    "r0": [("Approve", 0), ("Submit", 1), ("Approve", 4), ("Submit", 5), ("Approve", 100)],
}


def _import():
    rows = ["case,activity,time"]
    for i, (cid, steps) in enumerate(CASES.items()):
        for activity, hour in steps:
            day, h = divmod(hour, 24)
            rows.append(f"{cid},{activity},2024-03-{1 + i * 5 + day:02d}T{h:02d}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _cases(log, rules):
    def run(conn):
        return {"ids": sorted(c for (c,) in conn.execute("SELECT DISTINCT case_id FROM ce").fetchall())}
    return workspace._query_filtered(log, None, {"follows": rules}, run)["ids"]


def rule(mode="eventual", **hours):
    return {"from": "Submit", "to": "Approve", "mode": mode, **hours}


def test_eventual_rule_with_time_limits():
    log = _import()
    assert _cases(log, [rule(min_hours=24)]) == ["a30", "e60"]
    assert _cases(log, [rule(max_hours=12)]) == ["a10", "a2", "r0"]   # r0: first Submit (h1) → next Approve (h4)
    assert _cases(log, [rule(min_hours=5, max_hours=40)]) == ["a10", "a30"]
    assert _cases(log, [rule()]) == ["a10", "a2", "a30", "e60", "r0"]   # without a limit: order only


def test_direct_rule_with_time_limits_and_combination():
    log = _import()
    # e60 has Check in between, so it never has Submit directly followed by Approve.
    assert _cases(log, [rule("direct", min_hours=24)]) == ["a30"]
    assert _cases(log, [rule("direct", max_hours=3)]) == ["a2"]   # r0's first direct pair is 3 hours: excluded
    combined = [rule(max_hours=40), {"from": "Submit", "to": "Check", "mode": "eventual", "negate": True}]
    assert _cases(log, combined) == ["a10", "a2", "a30", "r0"]
    parsed = cohorts.parse_cohort({"follows": [rule(min_hours=1.5)]})
    assert parsed["follows"][0]["min_hours"] == 1.5 and "max_hours" not in parsed["follows"][0]


@pytest.mark.parametrize("bad", [
    rule(min_hours=-1), rule(max_hours="5"), rule(min_hours=True), rule(min_hours=5, max_hours=5),
    {**rule(max_hours=5), "negate": True}, {**rule(), "within": 3},
])
def test_invalid_time_rules(bad):
    with pytest.raises(EventLogWorkspaceError):
        cohorts.parse_cohort({"follows": [bad]})


def test_query_parameter(tmp_path):
    follows = json.dumps([{"from": "Submit", "to": "Approve", "mode": "eventual", "min_hours": 24}])
    code = r'''
import sys, urllib.parse
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time", "a,Submit,2024-01-01T00:00:00Z", "a,Approve,2024-01-01T02:00:00Z",
        "b,Submit,2024-01-02T00:00:00Z", "b,Approve,2024-01-04T00:00:00Z"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content="\n".join(rows).encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    base='/v1/event-logs/'+made.json()['log_id']+'/summary?follows='
    r=client.get(base+urllib.parse.quote(sys.argv[1]),headers=owner)
    assert r.status_code==200 and r.json()['cases']==1, r.text
    bad=sys.argv[1].replace('"min_hours": 24','"min_hours": -2')
    assert client.get(base+urllib.parse.quote(bad),headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code, follows], env=env, check=True, timeout=60)
