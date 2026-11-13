"""Declared CSV timestamp formats and detecting them from sample values."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_quality as quality
from server import event_log_timestamp_formats as formats
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _import(rows, **mapping):
    text = "case,activity,time\n" + "\n".join(rows) + "\n"
    return workspace.import_log(BytesIO(text.encode()), name="t", source_format="csv", mapping={**MAPPING, **mapping})


def _times(log_id):
    events = [e for _, evs in workspace.case_events(log_id) for e in evs]
    return sorted(e["timestamp"] if isinstance(e, dict) else e.timestamp for e in events)


def test_validate_accepts_known_patterns_and_refuses_others():
    for pattern, _order in formats.KNOWN:
        assert formats.validate(pattern) == pattern
    for bad in ("", "x" * 65, "%Y-%m", "%H:%M", "%d/%m/%Y %H:%M'; DROP", "%d/%m/%Y %Q", "%d/%m/%Y %I:%M",
                "%d/%m/%Y %H:%M %p", "%d/%m/%Y %d", None, 5, "%d/%m/%Y%"):
        with pytest.raises(formats.TimestampFormatError):
            formats.validate(bad)


def test_day_first_format_with_explicit_offset_imports_and_is_recorded():
    rows = ["c1,Receive,13/03/2024 09:05", "c1,Approve,13/03/2024 10:30:00", "c2,Receive,14/03/2024 08:00",
            "c2,Approve,31/02/2024 08:00", "c3,Receive,2024-03-14T08:00:00Z"]
    result = _import(rows, timestamp_format="%d/%m/%Y %H:%M", naive_timestamp_offset="+01:00")
    # Only values that match the pattern exactly are read: a seconds suffix, an impossible date and an ISO
    # value are rejected rather than guessed.
    assert result["events"] == 2
    report = quality.quality(result["log_id"])
    assert report["reports"][0]["transform"]["mapping"]["timestamp_format"] == "%d/%m/%Y %H:%M"
    rows_report = report["reports"][0]["rows"]
    assert rows_report["rejected"] == 3
    assert rows_report["rejected_by_reason"]["unparseable_timestamp"] == 3
    assert rows_report["rejected_by_reason"]["timestamp_without_utc_offset"] == 0
    summary = workspace.summary(result["log_id"])
    assert summary["first_event_at"] == "2024-03-13T08:05:00Z"


def test_format_without_offset_needs_a_decision():
    with pytest.raises(workspace.EventLogWorkspaceError, match="also choose the offset"):
        _import(["c1,Receive,03/13/2024 09:05 AM"], timestamp_format="%m/%d/%Y %I:%M %p")
    result = _import(["c1,Receive,03/13/2024 09:05 AM", "c1,Approve,03/13/2024 01:30 PM"],
                     timestamp_format="%m/%d/%Y %I:%M %p", naive_timestamp_offset="-05:00")
    assert result["events"] == 2
    summary = workspace.summary(result["log_id"])
    assert (summary["first_event_at"], summary["last_event_at"]) == ("2024-03-13T14:05:00Z", "2024-03-13T18:30:00Z")


def test_format_with_offset_reads_it_from_each_value():
    result = _import(["c1,A,2024-03-13 09:05:00 +01:00", "c1,B,2024-03-13 09:05:00 -0200", "c1,C,2024-03-13 09:05:00"],
                     timestamp_format="%Y-%m-%d %H:%M:%S %z")
    assert result["events"] == 2
    summary = workspace.summary(result["log_id"])
    assert (summary["first_event_at"], summary["last_event_at"]) == ("2024-03-13T08:05:00Z", "2024-03-13T11:05:00Z")


def test_bad_format_and_non_csv_are_refused():
    with pytest.raises(workspace.EventLogWorkspaceError, match="timestamp format"):
        _import(["c1,A,13/03/2024 09:05"], timestamp_format="%d/%m/%Y %H:%M' OR 1=1")
    with pytest.raises(workspace.EventLogWorkspaceError, match="CSV only"):
        workspace.import_log(BytesIO(b"<log/>"), name="x", source_format="xes", mapping={"timestamp_format": "%d/%m/%Y"})


def test_detect_suggests_unambiguous_formats_and_flags_ambiguity():
    day_first = formats.detect(["13/03/2024 09:05", "01/04/2024 17:45", "02/04/2024 08:00"])
    assert day_first["suggested"] == "%d/%m/%Y %H:%M" and not day_first["ambiguous"]
    assert day_first["candidates"][0]["first_value_reads_as"] == "2024-03-13 09:05:00"
    both = formats.detect(["03/04/2024 09:05", "05/06/2024 10:00"])
    assert both["ambiguous"] and both["suggested"] is None
    assert {c["order"] for c in both["candidates"]} >= {"day-first", "month-first"}
    sap = formats.detect(["04.03.2024 14:05:00", "05.03.2024 08:00:00"])
    assert sap["suggested"] == "%d.%m.%Y %H:%M:%S"
    oracle = formats.detect(["04-MAR-24 02.05.00 PM", "05-MAR-24 09.10.00 AM"])
    assert oracle["suggested"] == "%d-%b-%y %I.%M.%S %p"
    iso = formats.detect(["2024-03-04T14:05:00Z", "2024-03-04T15:05:00+01:00"])
    assert iso["iso_with_offset"] == 2 and iso["suggested"] is None and "ISO 8601" in iso["note"]
    mixed = formats.detect(["13/03/2024 09:05", "March 4th"])
    assert mixed["candidates"] == [] and "no known format" in mixed["note"]
    assert formats.detect([])["samples"] == 0


def test_sample_column_reads_the_start_of_a_file():
    text = "﻿case;time\nc1;13/03/2024 09:05\nc2;\nc3;14/03/2024 10:00\nc4;15/03/20"
    assert formats.sample_column(text, ";", "time") == ["13/03/2024 09:05", "14/03/2024 10:00"]
    with pytest.raises(formats.TimestampFormatError):
        formats.sample_column(text, ";", "missing")
    with pytest.raises(formats.TimestampFormatError):
        formats.sample_column(text, "x", "time")


def test_routes(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
body = "case,activity,time\nc1,A,13/03/2024 09:05\nc1,B,14/03/2024 10:00\nc2,A,15/03/2024 11:00\n"
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    path = "/v1/event-logs/timestamp-formats?column=time"
    assert client.post(path, content=body, headers=bearer).status_code == 403
    r = client.post(path, content=body, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["suggested"] == "%d/%m/%Y %H:%M"
    assert client.post("/v1/event-logs/timestamp-formats?column=nope", content=body, headers=owner).status_code == 422
    assert client.post(path, content=b"x" * (300 * 1024), headers=owner).status_code == 413
    q = ("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time"
         "&timestamp_format=%25d%2F%25m%2F%25Y%20%25H%3A%25M&naive_timestamp_offset=%2B00%3A00")
    imported = client.post(q, content=body.encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    assert imported.json()["events"] == 3
    log_id = imported.json()["log_id"]
    more = "case,activity,time\nc3,A,16/03/2024 12:00\n"
    appended = client.post(f"/v1/event-logs/{log_id}/append" + q[q.index("?"):].replace("&name=t", ""),
                           content=more.encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert appended.status_code == 200, appended.text
    bad = client.post(q.replace("%25H", "%25Q"), content=body.encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert bad.status_code == 422, bad.text
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
