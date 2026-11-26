"""Activity co-occurrence: shares, lift, pairs that go together and pairs that never do."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_cooccurrence as co
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(paths):
    rows = ["case,activity,time"]
    for i, path in enumerate(paths):
        for j, a in enumerate(path):
            rows.append(f"c{i},{a},2024-01-{1 + i % 28:02d}T{j:02d}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="c", source_format="csv", mapping=MAPPING)["log_id"]


def _log():
    """20 cases: all Open/Close. 10 Approve (6 of them also Ship), 10 Reject (never Ship), Escalate in 4 Rejects."""
    paths = []
    for i in range(20):
        if i < 10:
            paths.append(["Open", "Approve"] + (["Ship"] if i < 6 else []) + ["Close"])
        else:
            paths.append(["Open", "Reject"] + (["Escalate"] if i < 14 else []) + ["Close"])
    return _import(paths)


def test_pair_list_coverage_discloses_truncation():
    from io import BytesIO
    rows = ["case,activity,time"]
    for i in range(8):
        for j in range(6):
            rows.append(f"c{i},A{j},2024-01-01T00:{j:02d}:00Z")
        for j in range(6):
            rows.append(f"d{i},B{j},2024-01-01T00:{j:02d}:00Z")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="pairs",
                               source_format="csv", mapping=MAPPING)["log_id"]
    result = co.cooccurrence(log, limit=8)
    assert result["activities_in_scope"] == 12 and result["activities_truncated"]
    assert result["qualifying_never_together_pairs"] == 12
    assert result["pair_list_limit"] == 15
    all_pairs = co.cooccurrence(log, limit=12)
    assert all_pairs["qualifying_never_together_pairs"] == 36
    assert len(all_pairs["never_together"]) == 15


def test_matrix_lift_and_pairs():
    log = _log()
    r = co.cooccurrence(log, limit=6, min_support=3)
    assert r["cases"] == 20
    assert [a["activity"] for a in r["activities"]][:2] == ["Close", "Open"]
    row = {m["activity"]: m for m in r["matrix"]}
    ship = {w["activity"]: w for w in row["Ship"]["with"]}
    assert ship["Approve"]["share_of_a"] == 1.0 and ship["Approve"]["lift"] == round(6 * 20 / (6 * 10), 3)
    approve = {w["activity"]: w for w in row["Approve"]["with"]}
    assert approve["Ship"]["share_of_a"] == 0.6 and approve["Reject"]["both"] == 0 and approve["Reject"]["lift"] == 0.0
    assert approve["Approve"]["share_of_a"] == 1.0 and approve["Approve"]["lift"] is None
    assert approve["Close"]["lift"] == 1.0   # Close is in every case: independent of anything
    top = r["go_together"][0]
    assert {top["a"], top["b"]} in ({"Escalate", "Reject"}, {"Approve", "Ship"}) and top["lift"] == 2.0
    never = {frozenset((p["a"], p["b"])) for p in r["never_together"]}
    assert {frozenset(("Approve", "Reject")), frozenset(("Escalate", "Ship")), frozenset(("Approve", "Escalate"))} <= never


def test_support_limit_cohort_and_errors():
    log = _log()
    strict = co.cooccurrence(log, limit=6, min_support=5)
    assert all(p["both"] >= 5 for p in strict["go_together"])
    assert frozenset(("Escalate", "Ship")) not in {frozenset((p["a"], p["b"])) for p in strict["never_together"]}
    small = co.cooccurrence(log, limit=2)
    assert len(small["matrix"]) == 2 and len(small["matrix"][0]["with"]) == 2
    approved = co.cooccurrence(log, cohort={"activity": "Approve"}, limit=6)
    assert approved["cases"] == 10 and "Reject" not in [a["activity"] for a in approved["activities"]]
    for bad in ({"limit": 1}, {"limit": 26}, {"limit": True}, {"min_support": 0}):
        with pytest.raises(EventLogWorkspaceError):
            co.cooccurrence(log, **bad)
    one = _import([["A"]] * 3)
    with pytest.raises(EventLogWorkspaceError, match="two activities"):
        co.cooccurrence(one)


def test_owner_only_route(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = "case,activity,time\n" + "".join(f"c{i},A,2024-01-0{i+1}T00:00:00Z\nc{i},{'B' if i%2 else 'C'},2024-01-0{i+1}T01:00:00Z\n" for i in range(6))
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=doc.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    url='/v1/event-logs/'+made.json()['log_id']+'/co-occurrence'
    assert client.get(url).status_code in (401,403) and client.get(url,headers=bearer).status_code in (401,403)
    r=client.get(url+'?min_support=2',headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert {(p['a'],p['b']) for p in r.json()['never_together']}=={('B','C')}
    assert client.get(url+'?activity=B',headers=owner).json()['cases']==3
    assert client.get(url+'?limit=1',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
