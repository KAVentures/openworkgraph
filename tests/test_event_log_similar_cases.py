"""Similar cases: nearest recorded cases by path, with differences and durations."""
from __future__ import annotations

import os
import random
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_similar_cases as sim
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(cases):
    """``cases`` maps case ID to (path, hours between steps)."""
    rows = ["case,activity,time"]
    for i, (cid, (path, gap)) in enumerate(cases.items()):
        for step, activity in enumerate(path):
            minutes = int(step * gap * 60)
            rows.append(f"{cid},{activity},2024-01-{1 + i % 28:02d}T00:00:00Z".replace(
                "T00:00:00Z", f"T{minutes // 60:02d}:{minutes % 60:02d}:00Z"))
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="s", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_ranks_by_path_distance_then_duration():
    cases = {"target": (["A", "B", "C", "D"], 1),
             "same-fast": (["A", "B", "C", "D"], 0.5), "same-close": (["A", "B", "C", "D"], 1.1),
             "extra": (["A", "B", "X", "C", "D"], 1), "missing": (["A", "C", "D"], 1),
             "swap": (["A", "C", "B", "D"], 1), "far": (["Q", "R"], 1)}
    log = _import(cases)
    r = sim.similar_cases(log, "target", limit=5)
    ids = [c["case_id"] for c in r["similar"]]
    assert ids[:2] == ["same-close", "same-fast"]
    assert set(ids[2:4]) == {"extra", "missing"} and ids[4] == "swap" and "far" not in ids
    by = {c["case_id"]: c for c in r["similar"]}
    assert by["same-close"]["distance"] == 0 and by["same-close"]["same_path"] is True
    assert by["extra"]["distance"] == 1 and by["extra"]["more_of"] == [{"activity": "X", "times": 1}]
    assert by["missing"]["fewer_of"] == [{"activity": "B", "times": 1}] and by["missing"]["similarity"] == round(1 - 1 / 7, 4)
    assert by["swap"]["distance"] == 2 and by["swap"]["more_of"] == [] and by["swap"]["fewer_of"] == []
    assert r["same_path_cases"] == 2 and r["case"]["events"] == 4 and r["case"]["duration_seconds"] == 3 * 3600
    assert r["cases_searched"] == 6 and r["search_complete"] is True
    assert sim.similar_cases(log, "target", limit=50)["similar"][-1]["case_id"] == "far"


def test_matches_brute_force_on_random_logs():
    rng = random.Random(7)
    cases = {f"c{i}": ([rng.choice("ABCDE") for _ in range(rng.randint(1, 8))], 1) for i in range(120)}
    log = _import(cases)
    for target in ("c0", "c5", "c77"):
        r = sim.similar_cases(log, target, limit=15)
        mine = cases[target][0]
        expected = sorted(sim._distance(list(map(ord, mine)), list(map(ord, p))) for cid, (p, _) in cases.items()
                          if cid != target)[:15]
        assert [c["distance"] for c in r["similar"]] == expected
        assert r["paths_compared"] < r["distinct_paths"] or r["distinct_paths"] <= 15


def test_cohort_validation_and_errors():
    log = _import({"t": (["A", "B"], 1), "u": (["A", "B"], 1), "v": (["A", "C"], 1)})
    only_c = sim.similar_cases(log, "t", cohort={"activity": "C"})
    assert [c["case_id"] for c in only_c["similar"]] == ["v"]
    for bad in ({"case_id": ""}, {"case_id": "nope"}, {"limit": 0}, {"limit": 51}, {"limit": True}):
        args = {"case_id": "t", **bad}
        with pytest.raises(EventLogWorkspaceError):
            sim.similar_cases(log, args.pop("case_id"), **args)
    lone = _import({"only": (["A"], 1)})
    with pytest.raises(EventLogWorkspaceError, match="no other cases"):
        sim.similar_cases(lone, "only")


def test_owner_only_route(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = "case,activity,time\nt,A,2024-01-01T00:00:00Z\nt,B,2024-01-01T01:00:00Z\nu,A,2024-01-02T00:00:00Z\nu,B,2024-01-02T02:00:00Z\nv,A,2024-01-03T00:00:00Z\nv,C,2024-01-03T01:00:00Z\n"
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=doc.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    url='/v1/event-logs/'+made.json()['log_id']+'/similar-cases'
    assert client.get(url+'?case_id=t').status_code in (401,403)
    assert client.get(url+'?case_id=t',headers=bearer).status_code in (401,403)
    r=client.get(url+'?case_id=t&limit=2',headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert [c['case_id'] for c in r.json()['similar']]==['u','v']
    assert client.get(url+'?case_id=t&activity=C',headers=owner).json()['similar'][0]['case_id']=='v'
    assert client.get(url+'?case_id=missing',headers=owner).status_code==422
    assert client.get(url,headers=owner).status_code==422
    assert client.get(url+'?case_id=t&limit=99',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
