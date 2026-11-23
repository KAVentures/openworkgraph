"""Anonymized export: rare paths suppressed, new case IDs, coarser time, no attributes."""
from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_anonymized_export as ax
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import():
    """Five cases A,B,C; three A,C; one rare A,X,C by 'Dr Secret'. Case i starts on day i+1 at 09:00."""
    rows = ["case,activity,time,who,note"]
    paths = [["A", "B", "C"]] * 5 + [["A", "C"]] * 3 + [["A", "X", "C"]]
    for i, path in enumerate(paths):
        for step, activity in enumerate(path):
            who = "Dr Secret" if activity == "X" else ("alice" if step == 0 else "bob")
            rows.append(f"patient-{i},{activity},2024-03-{i + 1:02d}T{9 + step * 2:02d}:30:00Z,{who},private note {i}")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="p", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _read(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_rare_paths_are_suppressed_and_identifiers_removed():
    log = _import()
    r = ax.anonymized_export(log, min_cases=3)
    assert (r["cases_total"], r["cases_kept"], r["cases_suppressed"]) == (9, 8, 1)
    assert (r["variants_total"], r["variants_kept"], r["variants_suppressed"]) == (3, 2, 1)
    assert r["activities_removed"] == ["X"] and r["smallest_kept_group"] == 3 and r["events_kept"] == 21
    rows = _read(r["csv"])
    assert list(rows[0]) == ["case_id", "step", "activity", "timestamp"]
    assert len(rows) == 21 and len({row["case_id"] for row in rows}) == 8
    assert all(row["case_id"].startswith("C") for row in rows)
    for secret in ("patient-", "Dr Secret", "alice", "private note", "X"):
        assert secret not in r["csv"]
    # Relative time: every case starts on 2024-03-01 at midnight and keeps its two-hour gaps.
    firsts = [row for row in rows if row["step"] == "1"]
    assert {row["timestamp"] for row in firsts} == {"2024-03-01T00:00:00Z"}
    seconds = {row["timestamp"] for row in rows if row["step"] == "2"}
    assert seconds == {"2024-03-01T02:00:00Z"}


def test_time_and_resource_modes():
    log = _import()
    day = _read(ax.anonymized_export(log, min_cases=3, time_mode="day")["csv"])
    assert {row["timestamp"] for row in day} == {f"2024-03-{d:02d}" for d in range(1, 9)}
    exact = _read(ax.anonymized_export(log, min_cases=3, time_mode="exact")["csv"])
    assert "2024-03-01T09:30:00Z" in {row["timestamp"] for row in exact}
    named = ax.anonymized_export(log, min_cases=3, resource_mode="pseudonymize")
    rows = _read(named["csv"])
    assert named["columns"][-1] == "resource"
    assert {row["resource"] for row in rows} == {"Resource 01", "Resource 02"}
    assert "alice" not in named["csv"] and "bob" not in named["csv"]
    # One pseudonym per original resource: the first step is always the same person.
    assert len({row["resource"] for row in rows if row["step"] == "1"}) == 1


def test_activity_codes_and_synthetic_dates_keep_paths_and_elapsed_gaps():
    log = _import()
    result = ax.anonymized_export(log, min_cases=3, time_mode="elapsed", activity_mode="pseudonymize")
    rows = _read(result["csv"])
    assert result["synthetic_start"] == "2000-01-01T00:00:00Z"
    assert len(rows) == 21
    paths = {}
    for row in rows:
        paths.setdefault(row["case_id"], []).append(row["activity"])
        assert row["timestamp"] == f"2000-01-01T{2 * (int(row['step']) - 1):02d}:00:00Z"
    assert {row["activity"] for row in rows} == {"A000001", "A000002", "A000003"}
    assert sorted([len(path) for path in paths.values()]) == [2] * 3 + [3] * 5
    assert len({tuple(path) for path in paths.values()}) == 2
    assert len({path[0] for path in paths.values()}) == len({path[-1] for path in paths.values()}) == 1


def test_sequence_mode_retains_event_order_and_omits_elapsed_time():
    log = _import()
    result = ax.anonymized_export(log, min_cases=3, time_mode="sequence")
    assert result["synthetic_start"] == "2000-01-01T00:00:00Z"
    rows = _read(result["csv"])
    assert len(rows) == 21
    for row in rows:
        assert row["timestamp"] == f"2000-01-01T00:00:0{int(row['step']) - 1}Z"


def test_synthetic_date_supports_the_full_timestamp_span():
    rows = ["case,activity,time"]
    for case in ("one", "two"):
        rows.extend([f"{case},A,0001-01-01T00:00:00Z", f"{case},B,9999-12-31T23:59:59Z"])
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="long", source_format="csv",
                               mapping={k: v for k, v in MAPPING.items() if k != "resource_column"})["log_id"]
    result = ax.anonymized_export(log, min_cases=2, time_mode="elapsed")
    assert result["synthetic_start"] == "0001-01-01T00:00:00Z"
    assert {row["timestamp"] for row in _read(result["csv"])} == {
        "0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z",
    }


def test_summary_only_cohort_and_errors():
    log = _import()
    s = ax.anonymized_export(log, min_cases=2, summary_only=True)
    assert "csv" not in s and s["cases_kept"] == 8
    with pytest.raises(EventLogWorkspaceError, match="no path is followed"):
        ax.anonymized_export(log, min_cases=6)
    for bad in ({"min_cases": 1}, {"min_cases": True}, {"time_mode": "hour"}, {"resource_mode": "keep"},
                {"activity_mode": "mask"}):
        with pytest.raises(EventLogWorkspaceError):
            ax.anonymized_export(log, **bad)
    only_b = ax.anonymized_export(log, min_cases=2, cohort={"activity": "B"}, summary_only=True)
    assert only_b["cases_total"] == 5 and only_b["cases_kept"] == 5


def test_formula_cells_are_neutralized(tmp_path):
    rows = ["case,activity,time"] + [f"c{i},  =HYPERLINK(1),2024-01-01T0{i}:00:00Z" for i in range(3)]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="f", source_format="csv",
                               mapping={k: v for k, v in MAPPING.items() if k != "resource_column"})["log_id"]
    out = _read(ax.anonymized_export(log, min_cases=3)["csv"])
    assert {row["activity"] for row in out} == {"'  =HYPERLINK(1)"}


def test_owner_only_routes(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = "case,activity,time\n" + "".join(f"p{i},A,2024-01-0{i+1}T00:00:00Z\np{i},B,2024-01-0{i+1}T01:00:00Z\n" for i in range(4))
doc += "rare,A,2024-01-08T00:00:00Z\n"
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=doc.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    base='/v1/event-logs/'+made.json()['log_id']+'/anonymized-export'
    assert client.get(base).status_code in (401,403)
    assert client.get(base+'.csv',headers=bearer).status_code in (401,403)
    s=client.get(base+'?min_cases=2',headers=owner)
    assert s.status_code==200 and s.headers['cache-control']=='no-store', s.text
    assert s.json()['cases_kept']==4 and s.json()['cases_suppressed']==1 and 'csv' not in s.json()
    f=client.get(base+'.csv?min_cases=2&time_mode=day',headers=owner)
    assert f.status_code==200 and f.headers['content-type'].startswith('text/csv')
    assert f.headers['x-owg-anonymized']=='cases=4; suppressed=1; min_cases=2'
    assert 'rare' not in f.text and 'p0' not in f.text and f.text.count('\n')==9
    assert client.get(base+'?min_cases=1',headers=owner).status_code==422
    assert client.get(base+'?time_mode=hour',headers=owner).status_code==422
    assert client.get(base+'?activity_mode=mask',headers=owner).status_code==422
    coded = '?min_cases=2&time_mode=elapsed&activity_mode=pseudonymize'
    preview = client.get(base+coded, headers=owner)
    assert preview.status_code==200 and preview.json()['activity_mode']=='pseudonymize'
    assert preview.json()['synthetic_start']=='2000-01-01T00:00:00Z'
    share = client.get(base+'.csv'+coded, headers=owner)
    assert share.status_code==200 and share.headers['cache-control']=='no-store'
    assert ',A000001,2000-' in share.text and ',A000002,2000-' in share.text
    sequence = client.get(base+'.csv?min_cases=2&time_mode=sequence', headers=owner)
    assert sequence.status_code==200 and ',B,2000-01-01T00:00:01Z' in sequence.text
    assert client.get(base+'?min_cases=9',headers=owner).status_code==422
    filtered=client.get(base+'?min_cases=2&activity=B',headers=owner)
    assert filtered.status_code==200 and filtered.json()['cases_total']==4
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
