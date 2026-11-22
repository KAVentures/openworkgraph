"""Gzip-compressed uploads (log.xes.gz, events.csv.gz) are decompressed before import."""
from __future__ import annotations

import gzip
from io import BytesIO, StringIO

import pytest

from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
CSV = ("case,activity,time\n" + "\n".join(f"c{i},A,2024-01-0{1 + i}T08:00:00Z" for i in range(5))).encode()


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def test_gzipped_csv_and_xes_import_like_plain_files():
    plain = workspace.import_log(BytesIO(CSV), name="plain", source_format="csv", mapping=MAPPING)
    packed = workspace.import_log(BytesIO(gzip.compress(CSV)), name="packed", source_format="csv", mapping=MAPPING)
    assert packed["events"] == 5
    # Digests describe the decompressed bytes, so both uploads are recognised as the same file.
    assert packed["quality"]["content_sha256"] == plain["quality"]["content_sha256"]
    out = StringIO()
    workspace.export_log(plain["log_id"], "xes", out)
    xes = workspace.import_log(BytesIO(gzip.compress(out.getvalue().encode())), name="xes", source_format="xes")
    assert xes["events"] == 5
    more = ("case,activity,time\nc9,B,2024-02-01T08:00:00Z\n").encode()
    r = workspace.append_log(packed["log_id"], BytesIO(gzip.compress(more)), source_format="csv", mapping=MAPPING)
    assert r["added_events"] == 1


def test_decompression_is_bounded_and_corrupt_files_are_refused(monkeypatch):
    monkeypatch.setattr(workspace, "MAX_IMPORT_BYTES", 1000)
    bomb = gzip.compress(CSV + b"\n" * 5000)              # small compressed, more than 1000 bytes decompressed
    assert len(bomb) < 1000
    with pytest.raises(EventLogWorkspaceError, match="decompressed event log is larger"):
        workspace.import_log(BytesIO(bomb), name="bomb", source_format="csv", mapping=MAPPING)
    monkeypatch.setattr(workspace, "MAX_IMPORT_BYTES", 1 << 30)
    with pytest.raises(EventLogWorkspaceError, match="could not be decompressed"):
        workspace.import_log(BytesIO(gzip.compress(CSV)[:30]), name="cut", source_format="csv", mapping=MAPPING)
    assert workspace.list_logs()["logs"] == []
    assert not list((workspace.DATA_DIR).glob("owg-eventlog-*"))
