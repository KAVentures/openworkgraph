"""Excel (.xlsx) worksheets converted to CSV for the event-log import."""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime

import pytest
import xlsxwriter

from server import event_log_workspace as workspace
from server import event_log_xlsx as xlsx


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _workbook(rows=None, *, sheets=None, date1904=False):
    out = io.BytesIO()
    book = xlsxwriter.Workbook(out, {"in_memory": True, "date_1904": date1904})
    stamp = book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    day = book.add_format({"num_format": "dd/mm/yyyy"})
    for name, data in (sheets or {"Events": rows}).items():
        sheet = book.add_worksheet(name)
        for r, row in enumerate(data):
            for c, value in enumerate(row):
                if isinstance(value, datetime):
                    sheet.write_datetime(r, c, value, day if value.hour == 0 and value.minute == 0 else stamp)
                elif value is None:
                    continue
                else:
                    sheet.write(r, c, value)
    book.close()
    out.seek(0)
    return out


ROWS = [["case", "activity", "time", "amount", "approved", "note"],
        ["c1", "Receive", datetime(2024, 3, 4, 8, 0, 0), 120, True, "first"],
        ["c1", "Pay", datetime(2024, 3, 4, 9, 30, 15), 120.5, False, None],
        ["c2", "Receive", datetime(2024, 3, 5), 7, True, "comma, and \"quote\""]]


def test_values_dates_and_layout():
    r = xlsx.to_csv(_workbook(ROWS))
    assert r["sheet"] == "Events" and r["sheets"] == ["Events"] and r["rows"] == 3 and r["stored"] is False
    assert r["columns"] == ["case", "activity", "time", "amount", "approved", "note"]
    assert r["date_columns"] == ["time"] and r["dates_have_no_offset"] is True
    rows = list(csv.reader(io.StringIO(r["csv"])))
    assert rows[1] == ["c1", "Receive", "2024-03-04T08:00:00", "120", "TRUE", "first"]
    assert rows[2] == ["c1", "Pay", "2024-03-04T09:30:15", "120.5", "FALSE", ""]
    assert rows[3] == ["c2", "Receive", "2024-03-05T00:00:00", "7", "TRUE", 'comma, and "quote"']


def test_sheet_choice_1904_dates_and_empty_rows():
    book = _workbook(sheets={"Notes": [["just text"]], "Log": [["case", "activity", "time"], [None], ["c9", "A", datetime(2024, 1, 2, 3, 4, 5)]]},
                     date1904=True)
    r = xlsx.to_csv(book, "Log")
    assert r["sheets"] == ["Notes", "Log"] and r["rows"] == 1
    assert list(csv.reader(io.StringIO(r["csv"])))[1] == ["c9", "A", "2024-01-02T03:04:05"]
    assert xlsx.to_csv(_workbook(sheets={"Notes": [["x"]], "Log": [["y"]]}))["sheet"] == "Notes"
    with pytest.raises(xlsx.XlsxError, match="no worksheet with that name"):
        xlsx.to_csv(_workbook(ROWS), "Missing")


def test_converted_csv_imports_with_an_offset():
    r = xlsx.to_csv(_workbook(ROWS))
    mapping = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
               "attribute_columns": ["amount"], "naive_timestamp_offset": "+01:00"}
    log = workspace.import_log(io.BytesIO(r["csv"].encode()), name="from excel", source_format="csv", mapping=mapping)
    assert log["events"] == 3
    events = dict(workspace.case_events(log["log_id"]))
    assert events["c1"][0]["timestamp"].isoformat().startswith("2024-03-04T07:00:00")  # 08:00 at +01:00
    assert events["c1"][1]["attributes"] == {"amount": "120.5"}


def test_refuses_unsafe_or_wrong_files(monkeypatch):
    for data, message in ((b"not a zip", "not an .xlsx"), (_zip({"a.txt": b"x"}), "not an .xlsx")):
        with pytest.raises(xlsx.XlsxError, match=message):
            xlsx.to_csv(io.BytesIO(data))
    macro = _zip_from(_workbook(ROWS), {"xl/vbaProject.bin": b"\x00"})
    with pytest.raises(xlsx.XlsxError, match="macro-enabled"):
        xlsx.to_csv(macro)
    doctype = _zip_from(_workbook(ROWS), {"xl/sharedStrings.xml": b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><sst/>'})
    with pytest.raises(xlsx.XlsxError, match="document type"):
        xlsx.to_csv(doctype)
    monkeypatch.setattr(xlsx, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(xlsx.XlsxError, match="too large"):
        xlsx.to_csv(_workbook(ROWS))
    monkeypatch.setattr(xlsx, "MAX_TOTAL_BYTES", 10 ** 9)
    monkeypatch.setattr(xlsx, "MAX_ROWS", 2)
    with pytest.raises(xlsx.XlsxError, match="more than 2 rows"):
        xlsx.to_csv(_workbook(ROWS))


def _zip(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    out.seek(0)
    return out.getvalue()


def _zip_from(book, replace):
    src = zipfile.ZipFile(book)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in src.namelist():
            if name not in replace:
                z.writestr(name, src.read(name))
        for name, data in replace.items():
            z.writestr(name, data)
    out.seek(0)
    return out


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
import io, xlsxwriter
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
out = io.BytesIO(); book = xlsxwriter.Workbook(out, {"in_memory": True}); s = book.add_worksheet("Log")
s.write_row(0, 0, ["case", "activity"]); s.write_row(1, 0, ["c1", "A"]); book.close()
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post("/v1/event-logs/xlsx-to-csv", content=out.getvalue(), headers=bearer).status_code == 403
    got = client.post("/v1/event-logs/xlsx-to-csv", content=out.getvalue(), headers=owner)
    assert got.status_code == 200, got.text
    assert got.json()["csv"] == "case,activity\nc1,A\n" and got.json()["stored"] is False
    assert client.post("/v1/event-logs/xlsx-to-csv", content=b"nope", headers=owner).status_code == 422
    assert client.post("/v1/event-logs/xlsx-to-csv?sheet=Other", content=out.getvalue(), headers=owner).status_code == 422
    assert client.get("/v1/event-logs", headers=owner).json()["logs"] == []
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
