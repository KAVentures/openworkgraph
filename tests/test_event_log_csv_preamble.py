"""CSV exports with an Excel delimiter hint or title rows above the column names."""
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

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
TITLED = "Report: Orders\nExported 2024-02-01 by ana\ncase,activity,time\nc1,A,2024-01-01T00:00:00Z\nc1,B,2024-01-01T01:00:00Z\n"


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _activities(log_id):
    return [e["activity"] for _, evs in workspace.case_events(log_id) for e in evs]


@pytest.mark.parametrize("hint", ["sep=;\n", "sep=;\r\n", "﻿sep=;\n"])
def test_excel_delimiter_hint_is_skipped_and_used(hint, tmp_path):
    data = hint + "case;activity;time\nc1;A;2024-01-01T00:00:00Z\n"
    result = workspace.import_log(BytesIO(data.encode()), name="x", source_format="csv", mapping=MAPPING)
    assert _activities(result["log_id"]) == ["A"]          # the hint's ";" wins over the default ","
    assert not list(tmp_path.glob("owg-csv-header-*"))


def test_title_rows_need_the_header_row_and_are_recorded():
    with pytest.raises(EventLogWorkspaceError):
        workspace.import_log(BytesIO(TITLED.encode()), name="x", source_format="csv", mapping=MAPPING)
    result = workspace.import_log(BytesIO(TITLED.encode()), name="x", source_format="csv",
                                  mapping={**MAPPING, "header_row": 3})
    assert _activities(result["log_id"]) == ["A", "B"]
    assert quality.quality(result["log_id"])["reports"][0]["transform"]["mapping"]["header_row"] == 3
    assert "header_row" not in quality.quality(workspace.import_log(
        BytesIO(TITLED.split("\n", 2)[2].encode()), name="y", source_format="csv",
        mapping=MAPPING)["log_id"])["reports"][0]["transform"]["mapping"]
    sample = preview.preview_csv(TITLED.encode(), {**MAPPING, "header_row": 3})
    assert sample["accepted_events"] == 2


@pytest.mark.parametrize("value,message", [(0, "header row must be"), (51, "header row must be"),
                                           (True, "header row must be"), (9, "fewer lines")])
def test_header_row_is_validated(value, message):
    with pytest.raises(EventLogWorkspaceError, match=message):
        workspace.import_log(BytesIO(TITLED.encode()), name="x", source_format="csv",
                             mapping={**MAPPING, "header_row": value})


def test_format_detection_finds_the_header_below_title_rows():
    text = "Report\nExported\ncase,time\nc1,13/03/2024 09:05\nc2,14/03/2024 10:00\n"
    assert formats.sample_column(text, ",", "time") == ["13/03/2024 09:05", "14/03/2024 10:00"]
    assert formats.sample_column("sep=;\ncase;time\nc1;13/03/2024 09:05\n", ";", "time") == ["13/03/2024 09:05"]


def test_append_and_route(tmp_path):
    log = workspace.import_log(BytesIO(TITLED.encode()), name="x", source_format="csv",
                               mapping={**MAPPING, "header_row": 3})["log_id"]
    workspace.append_log(log, BytesIO(TITLED.replace("c1,", "c2,").encode()), source_format="csv",
                         mapping={**MAPPING, "header_row": 3})
    assert len(_activities(log)) == 4
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
data = open(r"%s", "rb").read()
with TestClient(app) as client:
    owner = {'Authorization': 'OWG-Session ' + local_auth.create_dashboard_session(), 'X-OWG-Local-Storage-Confirmed': 'true'}
    base = '/v1/event-logs/import?format=csv&name=a&case_column=case&activity_column=activity&timestamp_column=time'
    assert client.post(base + '&header_row=3', content=data, headers=owner).json()['events'] == 2
    assert client.post(base + '&header_row=99', content=data, headers=owner).status_code == 422
'''
    path = tmp_path / "titled.csv"
    path.write_text(TITLED)
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code % path], env=env, check=True, timeout=60)
