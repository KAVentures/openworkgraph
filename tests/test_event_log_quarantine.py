"""Explicit quarantine decisions for rejected CSV rows (#370 R6)."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_quality as quality
from server import event_log_quarantine as quarantine
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "who", "attribute_columns": ["region"]}
ROWS = "\n".join([
    "case,activity,time,who,region",
    "c1,Receive,2024-01-01T09:00:00Z,ann,north",
    "c1,Check,2024-01-01 11:00:00,bob,north",          # no offset
    "c2,Receive,2024-01-02 08:30:00,ann,south",        # no offset
    "c2,Close,2024-01-02T12:00:00+01:00,bob,south",
    ",Receive,2024-01-03T09:00:00Z,ann,south",         # missing case
    "c3,Receive,not a time,ann,south",                 # unreadable without an offset
]) + "\n"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _import(keep=True, **extra):
    mapping = {**MAPPING, **({"keep_rejected": True} if keep else {}), **extra}
    return workspace.import_log(BytesIO(ROWS.encode()), name="q", source_format="csv", mapping=mapping)


def test_rows_are_kept_only_when_asked_and_reported_with_reasons():
    plain = _import(keep=False)
    assert plain["rejected_events"] == 4 and plain["quality"]["rejected_rows_kept_for_review"] == 0
    assert quarantine.quarantine(plain["log_id"])["rows_kept"] == 0
    assert quality.quality(plain["log_id"])["reports"][0]["rows"]["rejected_rows_retained"] is False
    made = _import()
    review = quarantine.quarantine(made["log_id"])
    assert review["rows_kept"] == 4 and review["batches"] == [{"batch": 0, "rows": 4}]
    assert review["reasons"] == {"missing_case": 1, "timestamp_without_utc_offset": 3}
    first = review["rows"][0]
    assert first["case"] == "c1" and first["time"] == "2024-01-01 11:00:00" and first["resource"] == "bob"
    assert first["attributes"] == {"region": "north"} and first["reasons"] == ["timestamp_without_utc_offset"]
    rows = quality.quality(made["log_id"])["reports"][0]["rows"]
    assert rows["rejected_rows_retained"] is True and rows["rejected_rows_kept_for_review"] == 4
    page = quarantine.quarantine(made["log_id"], limit=3)
    assert len(page["rows"]) == 3 and page["next_after"] is not None
    rest = quarantine.quarantine(made["log_id"], after=page["next_after"], limit=3)
    assert len(rest["rows"]) == 1 and rest["next_after"] is None


def test_release_applies_one_recorded_offset_decision_through_the_append_path():
    made = _import()
    log = made["log_id"]
    result = quarantine.release(log, {"naive_timestamp_offset": "+01:00"})
    d = result["decision"]
    assert d["rows_considered"] == 4 and d["rows_released"] == 2 and d["rows_still_kept"] == 2
    assert d["events_added"] == 2 and d["duplicates_skipped"] == 0 and d["appended_batch"] == 1
    assert workspace.summary(log)["events"] == 4
    conn = workspace._connect()
    try:
        stamps = dict(conn.execute("SELECT activity, ts FROM events WHERE log_id = ? AND event_id LIKE 'a1:%'",
                                   [log]).fetchall())
    finally:
        conn.close()
    assert stamps["Check"].isoformat() == "2024-01-01T10:00:00"       # 11:00 at +01:00 is 10:00 UTC
    report = quality.quality(log)["reports"][1]
    assert report["kind"] == "quarantine_release"
    assert report["transform"]["mapping"]["naive_timestamp_offset"] == "+01:00"
    assert report["rows"]["timestamps_with_assumed_offset"] == 2
    left = quarantine.quarantine(log)
    assert left["reasons"] == {"missing_case": 1, "timestamp_without_utc_offset": 1}
    assert [x["kind"] for x in left["decisions"]] == ["release"]
    again = quarantine.release(log, {"naive_timestamp_offset": "+01:00"})          # nothing new becomes valid
    assert again["decision"]["rows_released"] == 0 and again["append"] is None
    gone = quarantine.discard(log, {"batch": 0})
    assert gone["decision"]["rows_discarded"] == 2 and quarantine.quarantine(log)["rows_kept"] == 0
    assert [x["kind"] for x in quarantine.quarantine(log)["decisions"]] == ["release", "release", "discard"]


def test_an_offset_decision_at_import_is_explicit_and_changes_the_transform():
    default = _import(keep=False)
    decided = _import(keep=False, naive_timestamp_offset="-05:00")
    assert decided["events"] == 4 and decided["rejected_events"] == 2
    q = decided["quality"]
    assert q["timestamps_with_assumed_offset"] == 2 and q["rejected_by_reason"]["timestamp_without_utc_offset"] == 0
    assert q["rejected_by_reason"]["unparseable_timestamp"] == 1
    assert q["transform_sha256"] != default["quality"]["transform_sha256"]
    assert "naive_timestamp_offset" not in quality.quality(default["log_id"])["reports"][0]["transform"]["mapping"]


def test_bad_decisions_and_other_formats_are_refused_and_rows_go_with_the_log():
    made = _import()
    log = made["log_id"]
    for bad in ({}, {"naive_timestamp_offset": "+1"}, {"naive_timestamp_offset": "+15:00"},
                {"naive_timestamp_offset": "Z", "batch": -1}, {"naive_timestamp_offset": "Z", "extra": 1}, []):
        with pytest.raises(workspace.EventLogWorkspaceError):
            quarantine.release(log, bad)
    with pytest.raises(workspace.EventLogWorkspaceError):
        workspace.import_log(BytesIO(ROWS.encode()), name="q", source_format="csv",
                             mapping={**MAPPING, "naive_timestamp_offset": "local"})
    xes = ('<log xmlns="http://www.xes-standard.org/"><trace><string key="concept:name" value="t"/><event>'
           '<string key="concept:name" value="A"/><date key="time:timestamp" value="2024-01-01T00:00:00Z"/></event></trace></log>')
    with pytest.raises(workspace.EventLogWorkspaceError, match="CSV only"):
        workspace.import_log(BytesIO(xes.encode()), name="x", source_format="xes", mapping={"keep_rejected": True})
    workspace.delete_log(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM quarantine_decisions").fetchone()[0] == 0
    finally:
        conn.close()


def test_appended_batches_keep_their_own_rejected_rows_up_to_the_limit(monkeypatch):
    made = _import()
    monkeypatch.setattr(workspace, "MAX_QUARANTINED_ROWS", 1)
    newer = "case,activity,time,who,region\nc9,Receive,2024-02-01 09:00:00,ann,east\nc9,Close,,ann,east\n" \
            "c9,Check,2024-02-01T10:00:00Z,ann,east\n"
    appended = workspace.append_log(made["log_id"], BytesIO(newer.encode()), source_format="csv",
                                    mapping={**MAPPING, "keep_rejected": True})
    assert appended["quality"]["rejected_rows_kept_for_review"] == 1
    review = quarantine.quarantine(made["log_id"], batch=1)
    assert review["batches"] == [{"batch": 0, "rows": 4}, {"batch": 1, "rows": 1}] and len(review["rows"]) == 1
    rows = quality.quality(made["log_id"])["reports"][1]["rows"]
    assert rows["rejected_rows_over_review_limit"] == 1


def test_real_http_routes_require_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01 02:00:00\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time&keep_rejected=true", content=doc,
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200 and made.json()["quality"]["rejected_rows_kept_for_review"] == 1, made.text
    base = "/v1/event-logs/" + made.json()["log_id"] + "/quarantine"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get(base, headers=bearer).status_code in (401, 403)
    assert client.post(base + "/release", json={"naive_timestamp_offset": "Z"}, headers=bearer).status_code in (401, 403)
    review = client.get(base, headers=headers)
    assert review.status_code == 200 and review.headers["cache-control"] == "no-store"
    assert review.json()["rows"][0]["activity"] == "B"
    assert client.post(base + "/release", json={"naive_timestamp_offset": "local"}, headers=headers).status_code == 422
    released = client.post(base + "/release", json={"naive_timestamp_offset": "Z"}, headers=headers)
    assert released.status_code == 200 and released.json()["decision"]["events_added"] == 1, released.text
    assert client.post(base + "/discard", json={}, headers=headers).json()["decision"]["rows_discarded"] == 0
    decided = client.post("/v1/event-logs/import?format=csv&name=y&case_column=case&activity_column=activity"
                          "&timestamp_column=time&naive_timestamp_offset=%2B02:00", content=doc,
                          headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert decided.json()["events"] == 2 and decided.json()["quality"]["timestamps_with_assumed_offset"] == 1
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
