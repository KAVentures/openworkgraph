"""Agents can read variants, the process map, the duration distribution and time patterns of a shared log,
with rare paths, activities and transitions withheld."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log():
    """15 cases on weekdays at 09:00 UTC: 8 Open>Approve>Close (2 h), 5 Open>Reject>Close (5 h),
    2 Open>Rare>Close (30 h)."""
    rows = ["case,activity,time"]
    days = [d for d in range(1, 31) if d not in (6, 7, 13, 14, 20, 21, 27, 28)]   # January 2024 weekdays
    for i in range(15):
        middle, hours = ("Approve", 2) if i < 8 else (("Reject", 5) if i < 13 else ("Rare", 30))
        day = days[i]
        rows.append(f"c{i},Open,2024-01-{day:02d}T09:00:00Z")
        rows.append(f"c{i},{middle},2024-01-{day:02d}T09:30:00Z")
        end_h = 9 + hours
        rows.append(f"c{i},Close,2024-01-{day + end_h // 24:02d}T{end_h % 24:02d}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="v", source_format="csv", mapping=MAPPING)["log_id"]


def test_variants_withhold_rare_paths():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.variants(log)
    sharing.set_share(log, "aggregates")
    r = sharing.variants(log)
    assert [(v["rank"], v["path"], v["cases"]) for v in r["variants"]] == [
        (1, ["Open", "Approve", "Close"], 8), (2, ["Open", "Reject", "Close"], 5)]
    assert r["distinct_variants"] == 3 and r["withheld"] == {"variants": 1, "cases": 2, "min_cases": 5}
    assert r["shown_cases_share"] == round(13 / 15, 4)
    assert sharing.variants(log, limit=1)["variants"][0]["cases"] == 8
    for bad in (0, 101, True):
        with pytest.raises(EventLogWorkspaceError):
            sharing.variants(log, limit=bad)


def test_map_withholds_rare_activity_and_its_transitions():
    log = _log()
    sharing.set_share(log, "aggregates")
    m = sharing.process_map(log)
    assert {a["activity"] for a in m["activities"]} == {"Open", "Approve", "Reject", "Close"}
    edges = {(t["from"], t["to"]): t["case_count"] for t in m["transitions"]}
    assert edges == {("Open", "Approve"): 8, ("Approve", "Close"): 8, ("Open", "Reject"): 5, ("Reject", "Close"): 5}
    assert m["withheld"]["activities"] == 1 and m["withheld"]["transitions"] == 2
    assert m["start_activities"] == {"Open": 15} and m["end_activities"] == {"Close": 15}
    assert "log" not in m and "Rare" not in repr(m)


def test_durations_and_time_patterns():
    log = _log()
    sharing.set_share(log, "aggregates")
    d = sharing.duration_distribution(log, [3 * 3600, 24 * 3600])
    assert [b["cases"] for b in d["bands"]] == [8, 5, 2]
    assert set(d["observed_duration_seconds"]) == {"median", "p90", "p95"}
    t = sharing.time_patterns(log, time_zone="Europe/Berlin", activity="Open")
    assert t["settings"]["time_zone"] == "Europe/Berlin" and t["settings"]["activity"] == "Open"
    assert sum(map(sum, t["events"])) == 15 and t["events"][0][10] > 0   # 09:00 UTC is 10:00 in Berlin
    with pytest.raises(EventLogWorkspaceError, match="at least 5 supporting cases"):
        sharing.time_patterns(log, time_zone="Europe/Berlin", activity="Rare")
    with pytest.raises(EventLogWorkspaceError):
        sharing.time_patterns(log, time_zone="Not/AZone")


def test_agent_routes(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-{1+i:02d}T0{j}:00:00Z" for i in range(8) for j, a in enumerate("AB" + ("C" if i < 5 else "D"))]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    base='/v1/process-intelligence/'+log
    assert client.get(base+'/variants',headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    r=client.get(base+'/variants',headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert [v['path'] for v in r.json()['variants']]==[['A','B','C']] and r.json()['withheld']['cases']==3
    m=client.get(base+'/process-map?paths=1',headers=bearer).json()
    assert {(t['from'],t['to']) for t in m['transitions']}=={('A','B'),('B','C')}, m
    d=client.get(base+'/duration-distribution?cutoffs_seconds=3600,10000',headers=bearer)
    assert d.status_code==200 and [b['cases'] for b in d.json()['bands']]==[0,8,0], d.text
    assert client.get(base+'/duration-distribution?cutoffs_seconds=x',headers=bearer).status_code==422
    assert client.get(base+'/time-patterns?time_zone=UTC',headers=bearer).status_code==200
    assert client.get(base+'/time-patterns?time_zone=UTC&activity=D',headers=bearer).status_code==422
    assert client.get(base+'/process-map?paths=0',headers=bearer).status_code==422
    assert client.get(base+'/variants').status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
