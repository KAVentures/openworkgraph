"""Importing or appending a file that was already loaded says so, without blocking the import."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_same_source as same_source
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _doc(n=6, start=1):
    return ("case,activity,time\n" + "\n".join(f"c{i},A,2024-01-{i:02d}T08:00:00Z" for i in range(start, start + n))).encode()


def _import(doc, name):
    return workspace.import_log(BytesIO(doc), name=name, source_format="csv", mapping=MAPPING)


def test_reimporting_the_same_file_names_the_earlier_log():
    first = _import(_doc(), "Invoices Q1")
    assert "same_source_as" not in first
    second = _import(_doc(), "Invoices again")
    assert [(m["log_id"], m["name"], m["batch"], m["this_log"]) for m in second["same_source_as"]] == [
        (first["log_id"], "Invoices Q1", 0, False)]
    assert second["same_source_as"][0]["loaded_at"].endswith("Z")
    assert len(workspace.list_logs()["logs"]) == 2                 # a notice, the import still happened
    third = _import(_doc() + b"\n", "One byte longer")
    assert "same_source_as" not in third                           # byte-for-byte, not by content


def test_appending_a_file_already_in_this_log_or_another():
    log = _import(_doc(), "Base")["log_id"]
    newer = _doc(4, start=7)
    other = _import(newer, "Newer export")["log_id"]
    r = workspace.append_log(log, BytesIO(newer), source_format="csv", mapping=MAPPING)
    assert [(m["log_id"], m["batch"], m["this_log"]) for m in r["same_source_as"]] == [(other, 0, False)]
    again = workspace.append_log(log, BytesIO(_doc()), source_format="csv", mapping=MAPPING)
    assert again["duplicate_events_skipped"] == 6
    assert [(m["log_id"], m["batch"], m["this_log"]) for m in again["same_source_as"]] == [(log, 0, True)]


def test_deleted_logs_are_not_named_and_matches_are_capped(monkeypatch):
    gone = _import(_doc(), "Gone")["log_id"]
    workspace.delete_log(gone)
    assert "same_source_as" not in _import(_doc(), "Fresh")
    monkeypatch.setattr(same_source, "MAX_MATCHES", 2)
    for k in range(3):
        _import(_doc(), f"Copy {k}")
    assert len(_import(_doc(), "Last")["same_source_as"]) == 2


def test_a_failing_lookup_never_fails_the_import(monkeypatch):
    _import(_doc(), "One")

    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("boom")
    assert same_source.matches(Broken(), "log_x", 0) == {}
