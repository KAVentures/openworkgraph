"""Exports that split the date and the time of day into two columns."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_import_preview as preview
from server import event_log_quality as quality
from server import event_log_timestamp_formats as formats
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

CSV = ("Order;Step;Posting Date;Time\n4711;Create;04.03.2024;14:05:00\n4711;Approve;04.03.2024;\n"
       "4712;Create;;09:00:00\n4712;Ship;05.03.2024;08:30:15\n")
MAPPING = {"case_column": "Order", "activity_column": "Step", "timestamp_column": "Posting Date",
           "time_column": "Time", "delimiter": ";", "timestamp_format": "%d.%m.%Y %H:%M:%S",
           "naive_timestamp_offset": "+01:00", "keep_rejected": True}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _events(log_id):
    return [(c, e["activity"], e["timestamp"].isoformat()) for c, evs in workspace.case_events(log_id) for e in evs]


def test_date_and_time_columns_are_joined_before_parsing():
    result = workspace.import_log(BytesIO(CSV.encode()), name="x", source_format="csv", mapping=MAPPING)
    assert _events(result["log_id"]) == [("4711", "Create", "2024-03-04T13:05:00+00:00"),
                                         ("4712", "Ship", "2024-03-05T07:30:15+00:00")]
    report = quality.quality(result["log_id"])["reports"][0]
    assert report["transform"]["mapping"]["time_column"] == "Time"
    # A date without a time does not match the pattern and is rejected, not read as midnight;
    # a time without a date is a missing timestamp.
    assert report["rows"]["rejected_by_reason"]["unparseable_timestamp"] == 1
    assert report["rows"]["rejected_by_reason"]["missing_timestamp"] == 1
    from server import event_log_quarantine as quarantine
    kept = quarantine.quarantine(result["log_id"])["rows"]
    assert sorted(r["time"] for r in kept) == ["", "04.03.2024"]


def test_iso_dates_with_a_time_column_and_the_time_column_is_validated():
    rows = "case,activity,date,time\nc1,A,2024-03-04,14:05:00Z\nc1,B,2024-03-04,15:00:00+01:00\n"
    result = workspace.import_log(BytesIO(rows.encode()), name="y", source_format="csv",
                                  mapping={"case_column": "case", "activity_column": "activity",
                                           "timestamp_column": "date", "time_column": "time"})
    assert [t for _, _, t in _events(result["log_id"])] == ["2024-03-04T14:00:00+00:00", "2024-03-04T14:05:00+00:00"]
    for bad, message in (({"time_column": "nope"}, "missing required columns: nope"),
                         ({"time_column": "date"}, "must differ")):
        with pytest.raises(EventLogWorkspaceError, match=message):
            workspace.import_log(BytesIO(rows.encode()), name="z", source_format="csv",
                                 mapping={"case_column": "case", "activity_column": "activity",
                                          "timestamp_column": "date", **bad})


def test_detection_and_preview_use_the_joined_value():
    samples = formats.sample_column("D;T\n04.03.2024;14:05:00\n05.03.2024;08:30:15\n", ";", "D", time_column="T")
    assert samples == ["04.03.2024 14:05:00", "05.03.2024 08:30:15"]
    assert formats.detect(samples)["suggested"] == "%d.%m.%Y %H:%M:%S"
    with pytest.raises(formats.TimestampFormatError, match="time-of-day column"):
        formats.sample_column("D;T\n04.03.2024;14:05\n", ";", "D", time_column="X")
    sample = preview.preview_csv(CSV.encode(), {k: v for k, v in MAPPING.items() if k != "keep_rejected"})
    assert sample["accepted_events"] == 2


def test_append_and_routes(tmp_path):
    log = workspace.import_log(BytesIO(CSV.encode()), name="x", source_format="csv", mapping=MAPPING)["log_id"]
    more = "Order;Step;Posting Date;Time\n4713;Create;06.03.2024;10:00:00\n"
    workspace.append_log(log, BytesIO(more.encode()), source_format="csv", mapping=MAPPING)
    assert ("4713", "Create", "2024-03-06T09:00:00+00:00") in _events(log)
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data = "case,activity,day,clock\nc1,A,03/04/2024,14:05\n".encode()
with TestClient(app) as client:
    owner = {'Authorization': 'OWG-Session ' + local_auth.create_dashboard_session(), 'X-OWG-Local-Storage-Confirmed': 'true'}
    d = client.post('/v1/event-logs/timestamp-formats?column=day&time_column=clock', content=data, headers=owner)
    assert d.status_code == 200 and d.json()['candidates'][0]['first_value'] == '03/04/2024 14:05', d.text
    base = ('/v1/event-logs/import?format=csv&name=a&case_column=case&activity_column=activity&timestamp_column=day'
            '&time_column=clock&timestamp_format=%25m/%25d/%25Y%20%25H:%25M&naive_timestamp_offset=Z')
    r = client.post(base, content=data, headers=owner)
    assert r.status_code == 200 and r.json()['events'] == 1, r.text
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
