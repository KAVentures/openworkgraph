"""Offline SAP change-document (CDHDR/CDPOS) projection into an OWG event log."""
import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.sap_change_documents import ExportError, project
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
ORDER = "0000012345"
OTHER = "0000012346"
HEADER = ["MANDANT", "OBJECTCLAS", "OBJECTID", "CHANGENR", "USERNAME", "UDATE", "UTIME", "TCODE", "CHANGE_IND"]
POSITION = ["MANDANT", "OBJECTCLAS", "OBJECTID", "CHANGENR", "TABNAME", "TABKEY", "FNAME", "CHNGIND",
            "VALUE_NEW", "VALUE_OLD"]


def csv_bytes(columns, rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue().encode()


def header(obj=ORDER, number="1000", date="20260105", time="093000", tcode="VA01", ind="I",
           cls="VERKBELEG", user="JSMITH"):
    return ["100", cls, obj, number, user, date, time, tcode, ind]


def position(obj=ORDER, number="1000", value="B", field="GBSTK", table="VBUK", old="A"):
    return ["100", "VERKBELEG", obj, number, table, "100" + obj, field, "U", value, old]


def run(headers, **kwargs):
    options = dict(namespace="erp-prod", object_class="VERKBELEG", objects=[ORDER], utc_offset="+01:00")
    options.update(kwargs)
    return project(csv_bytes(HEADER, headers), **options)


def test_change_documents_become_ordered_events_in_utc_without_users_or_values():
    content, report = run([header(), header(number="1001", time="101500", tcode="VA02", ind="U"),
                           header(obj=OTHER, number="2000")])
    rows = list(csv.DictReader(io.StringIO(content)))
    assert [r["activity"] for r in rows] == ["Created (VA01)", "Changed (VA02)"]
    # 09:30 at UTC+01:00 is 08:30 UTC.
    assert [r["timestamp"] for r in rows] == ["2026-01-05T08:30:00+00:00", "2026-01-05T09:15:00+00:00"]
    assert len({r["case_id"] for r in rows}) == 1 and rows[0]["case_id"].startswith("sap:")
    assert len({r["source_event_id"] for r in rows}) == 2
    for private in (ORDER, OTHER, "JSMITH", "1000", "1001"):
        assert private not in content
    assert report["unselected_rows"] == 1 and report["represented_objects"] == 1
    assert report["users_or_old_values_copied"] is False and report["source_completeness"] == "unknown"


def test_keys_are_stable_per_namespace_and_object_class():
    first = run([header()])[0]
    assert run([header()])[0] == first
    assert run([header()], namespace="erp-test")[0] != first
    assert run([header(cls="EINKBELEG")], object_class="EINKBELEG")[0] != first


def test_declared_status_field_names_the_activity_and_old_values_stay_out():
    positions = csv_bytes(POSITION, [position(value="C", old="SECRET_OLD"),
                                     position(field="NETWR", table="VBAK", value="999.00")])
    content, report = run([header(), header(number="1001", ind="U", tcode="VA02")],
                          positions=positions, status_field="VBUK.GBSTK")
    rows = list(csv.DictReader(io.StringIO(content)))
    assert [r["activity"] for r in rows] == ["Status: C", "Changed (VA02)"]
    assert "SECRET_OLD" not in content and "999.00" not in content
    assert report["status_events"] == 1 and report["status_positions"] == 1


def test_duplicate_rows_collapse_and_conflicting_ones_refuse_the_export():
    _, report = run([header(), header()])
    assert report["duplicate_rows"] == 1 and report["events"] == 1
    with pytest.raises(ExportError, match="conflicting header values"):
        run([header(), header(time="100000")])
    positions = csv_bytes(POSITION, [position(value="B"), position(value="C")])
    with pytest.raises(ExportError, match="two different values"):
        run([header()], positions=positions, status_field="VBUK.GBSTK")


@pytest.mark.parametrize("kwargs, message", [
    ({"utc_offset": ""}, "UTC offset"),
    ({"utc_offset": "+1:00"}, "UTC offset"),
    ({"utc_offset": "+15:00"}, "UTC offset"),
    ({"utc_offset": "+01:20"}, "UTC offset"),
    ({"objects": []}, "1–32 distinct"),
    ({"objects": [ORDER, ORDER]}, "1–32 distinct"),
    ({"objects": [" " + ORDER]}, "1–32 distinct"),
    ({"objects": [str(n) for n in range(33)]}, "1–32 distinct"),
    ({"namespace": "erp prod"}, "namespace"),
    ({"object_class": "verkbeleg"}, "object class"),
    ({"status_field": "VBUK.GBSTK"}, "status field needs the matching CDPOS"),
    ({"positions": b"OBJECTCLAS\n"}, "status field needs the matching CDPOS"),
])
def test_invalid_settings_are_refused(kwargs, message):
    with pytest.raises(ExportError, match=message):
        run([header()], **kwargs)


@pytest.mark.parametrize("row, message", [
    (header(date="2026-01-05"), "YYYYMMDD"),
    (header(time="9300"), "YYYYMMDD"),
    (header(date="20261305"), "YYYYMMDD"),
    (header(ind="D"), "change indicator"),
    (header(number="12a"), "change number"),
])
def test_invalid_selected_rows_refuse_the_whole_export(row, message):
    with pytest.raises(ExportError, match=message):
        run([row])


def test_missing_columns_and_no_selected_documents_are_refused():
    with pytest.raises(ExportError, match="CDHDR column headers"):
        project(b"OBJECTCLAS,OBJECTID\nVERKBELEG,1\n", namespace="erp", object_class="VERKBELEG",
                objects=[ORDER], utc_offset="+00:00")
    with pytest.raises(ExportError, match="No selected change documents"):
        run([header(obj=OTHER)])


def test_output_imports_into_an_event_log(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    content, _ = run([header(), header(number="1001", time="101500", tcode="VA02", ind="U"),
                      header(obj=OTHER, number="2000", time="080000")], objects=[ORDER, OTHER])
    made = workspace.import_log(io.BytesIO(content.encode()), name="sap", source_format="csv", mapping={
        "case_column": "case_id", "activity_column": "activity", "timestamp_column": "timestamp"})
    assert made["events"] == 3 and made["rejected_events"] == 0 and made["objects"] == 2


def test_cli_writes_a_new_file_and_refuses_to_overwrite(tmp_path):
    source = tmp_path / "cdhdr.csv"
    source.write_bytes(csv_bytes(HEADER, [header()]))
    output = tmp_path / "events.csv"
    command = [sys.executable, "scripts/sap_change_documents.py", str(source), "--namespace", "erp-prod",
               "--object-class", "VERKBELEG", "--object", ORDER, "--utc-offset", "+01:00", "--output", str(output)]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["events"] == 1 and output.exists()
    again = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert again.returncode == 2 and json.loads(again.stdout)["status"] == "refused"
    assert ORDER not in again.stdout
