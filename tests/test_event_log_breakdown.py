"""Breakdown table: case KPIs by one or two dimensions."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_breakdown as bd
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "attribute_columns": ["region", "amount"]}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import():
    """12 cases. Region north/south/east in turn; months Jan/Feb/Mar in turn (so region = month).
    Every 4th case repeats B and takes an hour longer. Region is recorded on the first event only."""
    rows = ["case,activity,time,region,amount"]
    for i in range(12):
        region = ["north", "south", "east"][i % 3]
        acts = ["A", "B", "C"] + (["B"] if i % 4 == 0 else [])
        for j, a in enumerate(acts):
            rows.append(f"c{i},{a},2024-0{1 + i % 3}-{1 + i:02d}T{8 + j:02d}:00:00Z,{region if j == 0 else ''},"
                        f"{i * 10 if j == 0 else ''}")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="b", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_one_dimension_with_totals_and_activity_share():
    log = _import()
    r = bd.breakdown(log, "event_attribute:region", with_activity="B")
    assert r["row_values"] == ["east", "north", "south"] and r["column_values"] == []
    cells = {c["row"]: c for c in r["cells"]}
    # north: c0, c3, c6, c9 -> c0 reworks; south: c1, c4(rework), c7, c10; east: c2, c5, c8(rework), c11
    assert cells["north"]["cases"] == 4 and cells["north"]["rework_rate"] == 0.25
    assert cells["north"]["median_hours"] == 2.0 and cells["north"]["events_per_case"] == 3.25
    assert cells["north"]["with_activity_share"] == 1.0 and cells["north"]["small"] is True
    assert r["overall"]["cases"] == 12 and r["overall"]["rework_rate"] == 0.25 and r["overall"]["small"] is False
    assert r["rows"]["label"] == "event attribute region"


def test_two_dimensions_weekday_and_month_order():
    log = _import()
    r = bd.breakdown(log, "start_month", "end_activity")
    assert r["row_values"] == ["2024-01", "2024-02", "2024-03"] and r["column_values"] == ["C", "B"]
    jan_b = next(c for c in r["cells"] if c["row"] == "2024-01" and c["column"] == "B")
    assert jan_b["cases"] == 1 and jan_b["median_hours"] == 3.0   # c0 is the only January case that repeats B
    assert sum(c["cases"] for c in r["cells"]) == 12
    assert [t["column"] for t in r["column_totals"]] == ["C", "B"] and r["column_totals"][1]["cases"] == 3
    w = bd.breakdown(log, "start_weekday")
    assert all(v in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] for v in w["row_values"])


def test_missing_other_and_limits(monkeypatch):
    log = _import()
    monkeypatch.setattr(bd, "MAX_ROWS", 2)
    r = bd.breakdown(log, "event_attribute:amount")
    assert r["row_values"][-1] == bd.OTHER and len(r["row_values"]) == 3
    assert sum(c["cases"] for c in r["cells"]) == 12
    missing = bd.breakdown(log, "attribute:region")
    assert missing["row_values"] == [bd.MISSING] and missing["cells"][0]["cases"] == 12
    times = bd.breakdown(log, "start_month")
    assert times["row_values"] == ["2024-02", "2024-03", bd.OTHER]   # time keeps the latest values


def test_cohort_dimensions_and_errors():
    log = _import()
    only = bd.breakdown(log, "start_month", cohort={"event_attribute": "region=north"})
    assert only["cases"] == 4 and only["row_values"] == ["2024-01"]
    dims = [d["dimension"] for d in bd.dimensions(log)["dimensions"]]
    assert dims[:len(bd.BUILT_IN)] == list(bd.BUILT_IN) and {"event_attribute:region", "event_attribute:amount"} <= set(dims)
    for args in (("",), ("nope",), ("attribute:",), ("start_month", "start_month"), ("x" * 3,)):
        with pytest.raises(EventLogWorkspaceError):
            bd.breakdown(log, *args)
    with pytest.raises(EventLogWorkspaceError, match="no case in this selection does"):
        bd.breakdown(log, "start_month", with_activity="Z")


def test_owner_only_routes(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = "case,activity,time,team\n" + "".join(f"c{i},A,2024-01-0{i+1}T00:00:00Z,{'red' if i%2 else 'blue'}\nc{i},B,2024-01-0{i+1}T0{i+1}:00:00Z,\n" for i in range(6))
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time&attribute_column=team',
        content=doc.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    base='/v1/event-logs/'+made.json()['log_id']+'/breakdown'
    assert client.get(base+'?rows=start_month').status_code in (401,403)
    assert client.get(base+'/dimensions',headers=bearer).status_code in (401,403)
    d=client.get(base+'/dimensions',headers=owner)
    assert d.status_code==200 and 'event_attribute:team' in [x['dimension'] for x in d.json()['dimensions']], d.text
    r=client.get(base+'?rows=event_attribute:team&columns=end_activity',headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert {c['row']:c['cases'] for c in r.json()['cells']}=={'blue':3,'red':3}
    f=client.get(base+'?rows=event_attribute:team&activity=B',headers=owner)
    assert f.status_code==200 and f.json()['cases']==6
    assert client.get(base+'?rows=bogus',headers=owner).status_code==422
    assert client.get(base,headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)


def test_tag_rework_and_duration_band_dimensions():
    from server import event_log_case_tags as case_tags
    log = _import()
    case_tags.add(log, {"tag": "vip", "case_ids": ["c0", "c1", "c2"]})
    r = bd.breakdown(log, "tag:vip", "rework")
    assert r["row_values"] == ["not tagged", "tagged"] and r["rows"]["label"] == "tag vip"
    assert set(r["column_values"]) == {"no repeats", "repeats an activity"}
    cells = {(c["row"], c["column"]): c["cases"] for c in r["cells"]}
    # c0, c4, c8 repeat B; c0 is also tagged.
    assert cells[("tagged", "repeats an activity")] == 1 and cells[("tagged", "no repeats")] == 2
    assert cells[("not tagged", "repeats an activity")] == 2 and cells[("not tagged", "no repeats")] == 7
    bands = bd.breakdown(log, "duration_band")
    # Cases take 2 hours, or 3 hours with the repeated B: all in the 1–24 hour band.
    assert bands["row_values"] == ["1 to 24 hours"] and bands["cells"][0]["cases"] == 12
    dims = [d["dimension"] for d in bd.dimensions(log)["dimensions"]]
    assert "tag:vip" in dims and "duration_band" in dims and "rework" in dims
    nobody = bd.breakdown(log, "tag:missing")
    assert nobody["row_values"] == ["not tagged"]
    with pytest.raises(EventLogWorkspaceError):
        bd.breakdown(log, "tag:")


def test_duration_bands_are_ordered():
    rows = ["case,activity,time"]
    spans = {"q": 0.5, "h": 5, "d": 50, "w": 300, "m": 1000}   # hours
    for cid, hours in spans.items():
        rows += [f"{cid},A,2024-01-01T00:00:00Z"]
        import datetime as _dt
        t = _dt.datetime(2024, 1, 1) + _dt.timedelta(hours=hours)
        rows += [f"{cid},B,{t.isoformat()}Z"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="b", source_format="csv",
                               mapping={k: v for k, v in MAPPING.items() if k != "attribute_columns"})["log_id"]
    r = bd.breakdown(log, "duration_band")
    assert r["row_values"] == ["under 1 hour", "1 to 24 hours", "1 to 7 days", "7 to 30 days", "30 days or more"]
