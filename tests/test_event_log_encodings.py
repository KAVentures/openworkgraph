"""CSV event logs in Windows-1252 or UTF-16 are decoded strictly before the normal CSV import."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_import_preview as preview
from server import event_log_quality as quality
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "Fall", "activity_column": "Tätigkeit", "timestamp_column": "Zeit", "delimiter": ";"}
TEXT = "Fall;Tätigkeit;Zeit;Notiz\nc1;Prüfung;2024-01-01T08:00:00Z;„ok“ – €5\nc1;Bestätigt;2024-01-01T09:00:00Z;\n"


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _activities(log_id):
    return [e["activity"] for _, events in workspace.case_events(log_id) for e in events]


@pytest.mark.parametrize("encoding,data", [("windows-1252", TEXT.encode("cp1252")), ("utf-16", TEXT.encode("utf-16")),
                                           ("utf-16", TEXT.encode("utf-16-be").join([b"\xfe\xff", b""])),
                                           ("utf-8", TEXT.encode("utf-8-sig"))])
def test_encodings_import_to_the_same_events(encoding, data, tmp_path):
    result = workspace.import_log(BytesIO(data), name="x", source_format="csv",
                                  mapping={**MAPPING, "attribute_columns": ["Notiz"], "encoding": encoding})
    assert _activities(result["log_id"]) == ["Prüfung", "Bestätigt"]
    first = next(workspace.case_events(result["log_id"]))[1][0]
    assert first["attributes"] == {"Notiz": "„ok“ – €5"}
    report = quality.quality(result["log_id"])["reports"][0]
    assert report["transform"]["mapping"].get("encoding") == (None if encoding == "utf-8" else encoding)
    assert not list(tmp_path.glob("owg-csv-utf8-*"))                  # the converted copy is removed


def test_wrong_or_missing_encoding_is_refused_with_a_hint():
    data = TEXT.encode("cp1252")
    with pytest.raises(EventLogWorkspaceError, match="not UTF-8 text; choose its encoding"):
        workspace.import_log(BytesIO(data), name="x", source_format="csv", mapping=MAPPING)
    # Invalid bytes after many valid rows are reported with their line, not as a raw parser error.
    late = ("Fall;Tätigkeit;Zeit\n".encode() + b"".join(f"c{i};A;2024-01-01T00:00:00Z\n".encode() for i in range(3000))
            + "c9;Prüfung;2024-01-01T01:00:00Z\n".encode("cp1252"))
    with pytest.raises(EventLogWorkspaceError, match=r"not UTF-8 text \(line 3002\)"):
        workspace.import_log(BytesIO(late), name="x", source_format="csv", mapping=MAPPING)
    # 0x81 is undefined in Windows-1252: refused with its offset, never replaced.
    with pytest.raises(EventLogWorkspaceError, match=r"not valid windows-1252 text \(byte 23\)"):
        workspace.import_log(BytesIO("Fall;Tätigkeit;Zeit\nc1;".encode("cp1252") + b"\x81;2024-01-01T00:00:00Z\n"),
                             name="x", source_format="csv", mapping={**MAPPING, "encoding": "windows-1252"})
    with pytest.raises(EventLogWorkspaceError, match="encoding must be"):
        workspace.import_log(BytesIO(data), name="x", source_format="csv", mapping={**MAPPING, "encoding": "latin-9"})


def test_append_and_preview_use_the_encoding():
    log = workspace.import_log(BytesIO(TEXT.encode("cp1252")), name="x", source_format="csv",
                               mapping={**MAPPING, "encoding": "windows-1252"})["log_id"]
    more = "Fall;Tätigkeit;Zeit\nc2;Prüfung;2024-01-02T08:00:00Z\n".encode("cp1252")
    workspace.append_log(log, BytesIO(more), source_format="csv", mapping={**MAPPING, "encoding": "windows-1252"})
    assert _activities(log).count("Prüfung") == 2 and workspace.summary(log)["events"] == 3
    sample = preview.preview_csv(TEXT.encode("utf-16"), {**MAPPING, "encoding": "utf-16"})
    assert sample["accepted_events"] == 2
    # A sample cut inside a UTF-16 code unit is still read as a partial preview.
    cut = TEXT.encode("utf-16")[:-1]
    assert preview.preview_csv(cut, {**MAPPING, "encoding": "utf-16"}, partial=True)["accepted_events"] >= 1
    with pytest.raises(EventLogWorkspaceError, match="not valid windows-1252 text"):
        preview.preview_csv(b"Fall;T\x81;Zeit\nc1;A;2024-01-01T00:00:00Z\n", {**MAPPING, "encoding": "windows-1252"})


def test_routes_accept_only_known_encodings(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data = "case,activity,time\nc1,Prüfung,2024-03-01T00:00:00Z\n".encode("cp1252")
with TestClient(app) as client:
    owner = {'Authorization': 'OWG-Session ' + local_auth.create_dashboard_session(), 'X-OWG-Local-Storage-Confirmed': 'true'}
    base = '/v1/event-logs/import?format=csv&name=a&case_column=case&activity_column=activity&timestamp_column=time'
    assert client.post(base + '&encoding=latin-9', content=data, headers=owner).status_code == 422
    assert client.post(base, content=data, headers=owner).status_code == 422
    r = client.post(base + '&encoding=windows-1252', content=data, headers=owner)
    assert r.status_code == 200 and r.json()['events'] == 1, r.text
    p = client.post('/v1/event-logs/import/preview?case_column=case&activity_column=activity&timestamp_column=time&encoding=windows-1252',
                    content=data, headers=owner)
    assert p.status_code == 200 and p.json()['accepted_events'] == 1, p.text
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
