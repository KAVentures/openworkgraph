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


def test_rfc3339_lowercase_t_and_z_are_read_like_uppercase():
    from shared.event_log_formats import parse_timestamp
    assert parse_timestamp("2024-01-01T00:00:00z") == parse_timestamp("2024-01-01T00:00:00Z")
    result = _import(["c1,A,2024-01-01T00:00:00z", "c1,B,2024-01-01t01:00:00Z", "c1,C,2024-01-01t02:00:00+01:00"])
    assert result["events"] == 3 and result["rejected_events"] == 0
    assert _times(result["log_id"])[0].isoformat() == "2024-01-01T00:00:00+00:00"
    # A lowercase "z" is an offset, not a naive time: an assumed offset does not shift it.
    shifted = _import(["c1,A,2024-01-01T00:00:00z"], naive_timestamp_offset="+05:00")
    assert workspace.summary(shifted["log_id"])["first_event_at"] == "2024-01-01T00:00:00Z"


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



@pytest.mark.parametrize("pattern,value,expected", [
    ("%d.%m.%Y %H:%M:%S,%f", "04.03.2024 14:05:00,123", "2024-03-04T14:05:00.123000+00:00"),
    ("%d-%m-%Y %H:%M:%S.%f", "13-03-2024 14:05:00.5", "2024-03-13T14:05:00.500000+00:00"),
    ("%d/%m/%Y %H:%M:%S.%f", "13/03/2024 14:05:00.250", "2024-03-13T14:05:00.250000+00:00"),
    ("%m/%d/%Y %H:%M:%S.%f", "03/13/2024 14:05:00.250", "2024-03-13T14:05:00.250000+00:00"),
])
def test_fractional_second_layouts_are_detected_and_keep_the_fraction(pattern, value, expected):
    assert formats.detect([value])["suggested"] == pattern
    result = _import([f"c1,A,{value}" if "," not in value else f'c1,A,"{value}"'],
                     timestamp_format=pattern, naive_timestamp_offset="Z")
    assert [e["timestamp"].isoformat() for _, evs in workspace.case_events(result["log_id"]) for e in evs] == [expected]


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


def test_unix_seconds_and_milliseconds_import_as_utc_without_an_offset_decision():
    rows = ["c1,Receive,1709561100", "c1,Approve,1709561160.250000", "c2,Receive, 1709564700 ",
            "c2,Approve,-1709564700", "c3,Receive,1.7e9", "c3,Approve,17095611000", "c4,Receive,2024-03-04T14:05:00Z",
            "c4,Approve,1709561100.1234567", "c5,Receive,"]
    result = _import(rows, timestamp_format="unix-seconds")
    assert result["events"] == 3            # sign, exponent, 11 digits, 7 decimals, ISO and empty are rejected
    events = {(c, e["activity"]): e["timestamp"] for c, evs in workspace.case_events(result["log_id"]) for e in evs}
    assert events[("c1", "Receive")].isoformat() == "2024-03-04T14:05:00+00:00"
    assert events[("c1", "Approve")].isoformat() == "2024-03-04T14:06:00.250000+00:00"
    assert events[("c2", "Receive")].isoformat() == "2024-03-04T15:05:00+00:00"
    report = quality.quality(result["log_id"])["reports"][0]
    assert report["transform"]["mapping"]["timestamp_format"] == "unix-seconds"
    assert report["rows"]["rejected_by_reason"]["unparseable_timestamp"] == 5
    ms = _import(["c1,Receive,1709561100123", "c1,Approve,1709561100123.5", "c1,Close,17095611001234"],
                 timestamp_format="unix-milliseconds")
    assert ms["events"] == 2
    stamps = sorted(e["timestamp"].isoformat() for _, evs in workspace.case_events(ms["log_id"]) for e in evs)
    assert stamps == ["2024-03-04T14:05:00.123000+00:00", "2024-03-04T14:05:00.123500+00:00"]


def test_unix_formats_validate_and_carry_their_own_offset():
    for name in ("unix-seconds", "unix-milliseconds"):
        assert formats.validate(name) == name and formats.has_offset(name)
    for bad in ("unix", "unix-minutes", "UNIX-SECONDS", "unix-seconds "):
        with pytest.raises(formats.TimestampFormatError):
            formats.validate(bad)
    # An offset decision is not needed; one given anyway does not shift Unix times.
    shifted = _import(["c1,Receive,1709561100"], timestamp_format="unix-seconds", naive_timestamp_offset="+05:00")
    assert workspace.summary(shifted["log_id"])["first_event_at"] == "2024-03-04T14:05:00Z"


def test_detect_offers_unix_time_for_plausible_values_only():
    seconds = formats.detect(["1709561100", "1709564700.5"])
    assert seconds["suggested"] == "unix-seconds" and seconds["candidates"][0]["order"] == "unix-time"
    assert seconds["candidates"][0]["first_value_reads_as"] == "2024-03-04 14:05:00 UTC"
    ms = formats.detect(["1709561100123", "1709564700000"])
    assert [c["pattern"] for c in ms["candidates"]] == ["unix-milliseconds"]
    # Short numbers, mixed lengths and signs are not offered as Unix time.
    for values in (["12345"], ["1709561100", "2024-03-04"], ["-1709561100"], ["1709561100", "1709561100123"]):
        assert not any(c["order"] == "unix-time" for c in formats.detect(values)["candidates"]), values


def test_quarantined_rows_release_with_a_unix_format(tmp_path):
    from server import event_log_quarantine as quarantine
    text = "case,activity,time\nc1,Receive,2024-03-04T14:00:00Z\nc1,Approve,1709561100\n"
    log = workspace.import_log(BytesIO(text.encode()), name="q", source_format="csv",
                               mapping={**MAPPING, "keep_rejected": True})["log_id"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="leave the assumed offset blank"):
        quarantine.release(log, {"timestamp_format": "unix-seconds", "naive_timestamp_offset": "+01:00"})
    result = quarantine.release(log, {"timestamp_format": "unix-seconds"})
    assert result["decision"]["rows_released"] == 1 and result["decision"]["timestamp_format"] == "unix-seconds"
    assert workspace.summary(log)["last_event_at"] == "2024-03-04T14:05:00Z"


def test_excel_serial_numbers_import_with_an_offset_decision():
    rows = ["c1,Receive,45355.5868055556", "c1,Approve,45355.75", "c2,Receive,45356", "c2,Approve,-45356",
            "c3,Receive,4.5e4", "c3,Approve,1234567", "c4,Receive,2024-03-04T14:05:00Z"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="also choose the offset"):
        _import(rows, timestamp_format="excel-serial")
    result = _import(rows, timestamp_format="excel-serial", naive_timestamp_offset="+01:00")
    assert result["events"] == 3            # sign, exponent, 7 digits and ISO are rejected
    events = {(c, e["activity"]): e["timestamp"] for c, evs in workspace.case_events(result["log_id"]) for e in evs}
    # 45355.5868055556 is 2024-03-04 14:05:00 local (rounded to the millisecond), 13:05 UTC.
    assert events[("c1", "Receive")].isoformat() == "2024-03-04T13:05:00+00:00"
    assert events[("c1", "Approve")].isoformat() == "2024-03-04T17:00:00+00:00"
    assert events[("c2", "Receive")].isoformat() == "2024-03-04T23:00:00+00:00"
    assert formats.validate("excel-serial") == "excel-serial" and not formats.has_offset("excel-serial")


def test_detect_offers_excel_serial_numbers():
    found = formats.detect(["45355.5868055556", "45356"])
    assert found["suggested"] == "excel-serial" and not found["candidates"][0]["has_offset"]
    assert found["candidates"][0]["first_value_reads_as"] == "2024-03-04 14:05:00"
    for values in (["1234"], ["45355", "1709561100"], ["123456"]):
        assert not any(c["pattern"] == "excel-serial" for c in formats.detect(values)["candidates"]), values
