"""Activity and resource statistics CSV: every row, spreadsheet-safe, follows the case filter."""
from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_statistics_export as stats
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    """4 cases Open(ann) > Check(bob) > Close(ann); c0 checks twice; c3 has no resource on Close."""
    rows = ["case,activity,time,who"]
    for i in range(4):
        steps = [("Open", "ann"), ("Check", "bob")] + ([("Check", "bob")] if i == 0 else []) + [("Close", "" if i == 3 else "ann")]
        rows += [f"c{i},{a},2024-01-0{1 + i}T{8 + 2 * j:02d}:00:00Z,{w}" for j, (a, w) in enumerate(steps)]
    rows.append("c4,=SUM(1),2024-01-08T08:00:00Z,@eve")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="s", source_format="csv", mapping=MAPPING)["log_id"]



def test_activities_csv():
    log = _log()
    text, meta = stats.activities_csv(log)
    table = {r["activity"]: r for r in csv.DictReader(io.StringIO(text))}
    assert meta == {"cases": 5, "rows": 4, "written": 4, "truncated": False}
    check = table["Check"]
    assert (check["events"], check["cases"], check["case_share"], check["cases_with_repeats"]) == ("5", "4", "0.8", "1")
    assert check["median_wait_before_seconds"] == "7200.0" and check["resources"] == "1"
    assert table["Open"]["starts_case_share"] == "0.8" and table["Close"]["ends_case_share"] == "0.8"
    assert table["Open"]["median_wait_before_seconds"] == "" and table["Open"]["first_at"] == "2024-01-01T08:00:00Z"
    assert "'=SUM(1)" in table and list(table)[0] == "Check"


def test_resources_csv_and_filter():
    log = _log()
    text, meta = stats.resources_csv(log)
    table = {r["resource"]: r for r in csv.DictReader(io.StringIO(text))}
    assert set(table) == {"ann", "bob", stats.NO_RESOURCE, "'@eve"} and meta["rows"] == 4
    assert (table["ann"]["events"], table["ann"]["cases"], table["ann"]["activities"]) == ("7", "4", "2")
    assert table["bob"]["most_frequent_activity"] == "Check" and table["bob"]["event_share"] == str(round(5 / 14, 6))
    filtered, _ = stats.activities_csv(log, cohort={"activity": "Close"})
    assert {r["activity"] for r in csv.DictReader(io.StringIO(filtered))} == {"Open", "Check", "Close"}
    with pytest.raises(EventLogWorkspaceError, match="no cases"):
        stats.resources_csv(log, cohort={"started_from": "2030-01-01T00:00:00Z"})


def test_truncation(monkeypatch):
    log = _log()
    monkeypatch.setattr(stats, "MAX_ROWS", 2)
    text, meta = stats.activities_csv(log)
    assert meta["truncated"] and meta["written"] == 2 and len(text.strip().splitlines()) == 3


def test_routes_are_owner_only(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(3)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    for kind in ('activities','resources'):
        url='/v1/event-logs/'+log+'/'+kind+'.csv'
        assert client.get(url,headers=bearer).status_code in (401,403)
        r=client.get(url,headers=owner)
        assert r.status_code==200 and r.headers['content-type'].startswith('text/csv') and r.headers['cache-control']=='no-store', r.text
        assert r.headers['x-owg-rows'].startswith('rows=1;') and kind+'.csv' in r.headers['content-disposition']
        assert client.get(url+'?started_from=2030-01-01T00:00:00Z',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
