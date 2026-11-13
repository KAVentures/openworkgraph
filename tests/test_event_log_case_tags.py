"""Analyst tags on cases of a saved event log."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_analytics as analytics
from server import event_log_case_tags as tags
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(name="t"):
    rows = []
    for i in range(10):
        for k, activity in enumerate(["Receive", "Escalate", "Pay"] if i % 3 == 0 else ["Receive", "Pay"]):
            rows.append([f"c{i}", activity, (START + timedelta(days=i, hours=k)).isoformat()])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def test_tag_by_id_and_by_filter_then_filter_and_remove():
    log = _log()
    r = tags.add(log, {"tag": " checked ", "note": "looked fine", "case_ids": ["c1", "c2", "c2", "nope"]})
    assert r["tag"] == "checked" and r["tagged"] == 2 and r["unknown_case_ids"] == 1 and r["already_tagged"] == 0
    assert r["tags"] == [{"tag": "checked", "cases": 2, "last_tagged_at": r["tags"][0]["last_tagged_at"],
                          "cases_with_note": 2}]
    # Re-tagging replaces the note instead of duplicating the row.
    again = tags.add(log, {"tag": "checked", "case_ids": ["c2", "c3"]})
    assert again["tagged"] == 2 and again["already_tagged"] == 1
    assert {t["tag"]: t["cases"] for t in again["tags"]} == {"checked": 3}
    by_filter = tags.add(log, {"tag": "escalated", "cohort": {"activity": "Escalate"}})
    assert by_filter["tagged"] == 4
    # The tag key of the case filter selects tagged cases in any analysis.
    assert analytics.kpis(log, cohort={"tag": "escalated"})["cases"] == 4
    assert analytics.kpis(log, cohort={"tag": "checked", "activity": "Escalate"})["cases"] == 1  # c3
    assert workspace.summary(log, cohort={"tag": "nothing"})["cases"] == 0
    listed = tags.listing(log, "checked")
    assert sorted(c["case_id"] for c in listed["cases"]) == ["c1", "c2", "c3"]
    assert {c["case_id"]: c["note"] for c in listed["cases"]}["c1"] == "looked fine"
    assert {c["case_id"]: c["note"] for c in listed["cases"]}["c2"] == ""
    removed = tags.remove(log, {"tag": "checked", "case_ids": ["c1"]})
    assert removed["removed"] == 1 and {t["tag"]: t["cases"] for t in removed["tags"]} == {"checked": 2, "escalated": 4}
    assert tags.remove(log, {"tag": "escalated"})["removed"] == 4
    assert [t["tag"] for t in tags.listing(log)["tags"]] == ["checked"]


def test_tags_are_per_log_and_deleted_with_the_log():
    first, second = _log("a"), _log("b")
    tags.add(first, {"tag": "x", "case_ids": ["c1"]})
    assert tags.listing(second)["tags"] == []
    assert analytics.kpis(second, cohort={"tag": "x"})["cases"] == 0
    workspace.delete_log(first)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM case_tags").fetchone()[0] == 0
    finally:
        conn.close()


def test_limits_and_validation(monkeypatch):
    log = _log()
    for bad in (None, {}, {"tag": "x"}, {"tag": "x", "case_ids": ["c1"], "cohort": {"activity": "Pay"}},
                {"tag": "", "case_ids": ["c1"]}, {"tag": "a\nb", "case_ids": ["c1"]}, {"tag": "x" * 61, "case_ids": ["c1"]},
                {"tag": "x", "case_ids": []}, {"tag": "x", "case_ids": ["nope"]}, {"tag": "x", "note": "n" * 501, "case_ids": ["c1"]},
                {"tag": "x", "cohort": {"activity": "Nowhere"}}, {"tag": "x", "cohort": {"bogus": 1}},
                {"tag": "x", "case_ids": ["c1"], "extra": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            tags.add(log, bad)
    monkeypatch.setattr(tags, "MAX_TAGS_PER_LOG", 1)
    tags.add(log, {"tag": "one", "case_ids": ["c1"]})
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most 1 tags"):
        tags.add(log, {"tag": "two", "case_ids": ["c1"]})
    tags.add(log, {"tag": "one", "case_ids": ["c2"]})  # an existing tag can still grow
    monkeypatch.setattr(tags, "MAX_TAGGED_ROWS_PER_LOG", 3)
    with pytest.raises(workspace.EventLogWorkspaceError, match="case tags"):
        tags.add(log, {"tag": "one", "case_ids": ["c3", "c4"]})
    for bad in ({"tag": "x", "case_ids": []}, {"tag": ""}, {"case_ids": ["c1"]}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            tags.remove(log, bad)
    with pytest.raises(workspace.EventLogWorkspaceError):
        tags.listing(log, limit=0)
    with pytest.raises(workspace.EventLogWorkspaceError):
        tags.listing("log_" + "0" * 16)


def test_routes_require_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{'A' if i else 'B'},2024-03-04T08:00:0{i}Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    base = f"/v1/event-logs/{imported.json()['log_id']}"
    body = {"tag": "odd", "case_ids": ["c1"]}
    assert client.post(base + "/case-tags", json=body, headers=bearer).status_code == 403
    assert client.get(base + "/case-tags", headers=bearer).status_code == 403
    added = client.post(base + "/case-tags", json=body, headers=owner)
    assert added.status_code == 200, added.text
    assert client.get(base + "/summary?tag=odd", headers=owner).json()["cases"] == 1
    assert client.get(base + "/case-tags?tag=odd", headers=owner).json()["cases"][0]["case_id"] == "c1"
    assert client.post(base + "/case-tags/remove", json={"tag": "odd"}, headers=owner).json()["removed"] == 1
    assert client.post(base + "/case-tags", json={"tag": "odd"}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
