"""Hidden activities: left out of case-centric analyses, stored events unchanged, deleted with the log."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_activity_mappings as mappings
from server import event_log_hidden_activities as hidden
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    """6 cases Open > Sync > Close; case c5 only has Sync events."""
    rows = ["case,activity,time"]
    for i in range(5):
        rows += [f"c{i},{a},2024-01-0{1 + i}T{8 + j:02d}:00:00Z" for j, a in enumerate(["Open", "Sync", "Close"])]
    rows += ["c5,Sync,2024-01-09T08:00:00Z", "c5,Sync,2024-01-09T09:00:00Z"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="h", source_format="csv", mapping=MAPPING)["log_id"]


def test_hiding_removes_events_from_analyses_and_showing_restores_them():
    log = _log()
    assert "hidden_activities" not in workspace.summary(log)
    r = hidden.set_hidden(log, {"activities": ["Sync"]})
    assert [h["activity"] for h in r["hidden"]] == ["Sync"] and r["events_hidden"] == 7 and r["events_total"] == 17
    assert {a["activity"]: a["hidden"] for a in r["activities"]} == {"Sync": True, "Open": False, "Close": False}
    s = workspace.summary(log)
    assert (s["events"], s["cases"]) == (10, 5)
    assert s["hidden_activities"] == {"activities": ["Sync"], "events_hidden": 7, "cases_with_only_hidden_events": 1,
                                      "note": "These activities are left out of this result; the stored events are unchanged."}
    assert [v["path"] for v in workspace.variants(log)["variants"]] == [["Open", "Close"]]
    assert hidden.hidden(log)["hidden"][0]["hidden_at"] == r["hidden"][0]["hidden_at"]
    assert hidden.set_hidden(log, {"activities": []})["hidden"] == []
    assert workspace.summary(log)["events"] == 17 and "hidden_activities" not in workspace.summary(log)


def test_hiding_matches_stored_names_before_a_mapping_relabels_them():
    log = _log()
    made = mappings.create(log, {"name": "m", "groups": [{"label": "Work", "activities": ["Open", "Sync"]}]})
    mappings.apply_mapping(log, made["mapping_id"])
    hidden.set_hidden(log, {"activities": ["Sync"]})
    s = workspace.summary(log)
    assert s["events"] == 10 and "activity_mapping" in s and "hidden_activities" in s
    assert {a["activity"] for a in workspace.activities(log)["activities"]} == {"Work", "Close"}
    with pytest.raises(EventLogWorkspaceError, match="not an activity"):
        hidden.set_hidden(log, {"activities": ["Work"]})       # a mapped label is not a stored name


def test_validation():
    log = _log()
    for bad, match in (({"activities": ["Nope"]}, "not an activity"), ({"activities": ["Sync", "Sync"]}, "only once"),
                       ({"activities": ["Open", "Sync", "Close"]}, "at least one"), ({"names": []}, "invalid"),
                       ({"activities": [""]}, "invalid"), (["Sync"], "invalid")):
        with pytest.raises(EventLogWorkspaceError, match=match):
            hidden.set_hidden(log, bad)
    with pytest.raises(EventLogWorkspaceError):
        hidden.hidden("log_0000000000000000")


def test_search_stored_names_outside_initial_choices_even_after_hiding_and_mapping(monkeypatch):
    log = _log()
    monkeypatch.setattr(hidden, "MAX_LISTED", 1)
    made = mappings.create(log, {"name": "m", "groups": [{"label": "Work", "activities": ["Close"]}]})
    mappings.apply_mapping(log, made["mapping_id"])
    hidden.set_hidden(log, {"activities": ["Close"]})
    assert hidden.search_original_activities(log, {"search": "Close"})["activities"] == [{"activity": "Close", "events": 5}]
    assert hidden.search_original_activities(log, {"search": "Work"})["activities"] == []
    for bad in ({"search": ""}, {"search": "Close", "after": None}, {"search": "Close", "extra": 1}):
        with pytest.raises(EventLogWorkspaceError):
            hidden.search_original_activities(log, bad)


def test_search_original_activity_choices_paginates():
    rows = ["case,activity,time"] + [f"c{i},Activity {i:03},2024-01-01T00:00:00Z" for i in range(125)]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="choices", source_format="csv", mapping=MAPPING)["log_id"]
    first = hidden.search_original_activities(log, {"search": "Activity"})
    assert len(first["activities"]) == 100 and first["next_after"] == "Activity 099"
    second = hidden.search_original_activities(log, {"search": "Activity", "after": first["next_after"]})
    assert len(second["activities"]) == 25 and second["activities"][0]["activity"] == "Activity 100"
    assert second["next_after"] is None


def test_hidden_labels_outside_initial_choices_remain_selected_and_stale_edits_fail(monkeypatch):
    log = _log()
    monkeypatch.setattr(hidden, "MAX_LISTED", 1)
    initial = hidden.hidden(log)
    hidden.set_hidden(log, {"activities": ["Close"], "expected_sha256": initial["definition_sha256"]})
    result = hidden.hidden(log)
    assert [(a["activity"], a["hidden"]) for a in result["activities"]] == [("Sync", False), ("Close", True)]
    with pytest.raises(hidden.HiddenActivitiesConflict):
        hidden.set_hidden(log, {"activities": ["Sync"], "expected_sha256": initial["definition_sha256"]})
    assert [a["activity"] for a in hidden.hidden(log)["hidden"]] == ["Close"]
    current = result["definition_sha256"]
    assert {a["activity"] for a in hidden.set_hidden(log, {"activities": ["Sync", "Close"], "expected_sha256": current})["hidden"]} == {"Sync", "Close"}
    assert hidden.set_hidden(log, {"activities": []})["hidden"] == []  # legacy callers remain supported


def test_deleting_the_log_deletes_the_list():
    log = _log()
    hidden.set_hidden(log, {"activities": ["Sync"]})
    workspace.delete_log(log)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM hidden_activities").fetchone()[0] == 0
        finally:
            conn.close()


def test_routes_are_owner_only(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-0{1+i}T0{j}:00:00Z" for i in range(4) for j, a in enumerate(["A","Ping","B"])]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/hidden-activities'
    assert client.get(url,headers=bearer).status_code in (401,403)
    assert client.post(url,json={"activities":["Ping"]},headers=bearer).status_code in (401,403)
    assert client.post(url+'/search',json={'search':'Ping'},headers=bearer).status_code in (401,403)
    search=client.post(url+'/search',json={'search':'Ping'},headers=owner)
    assert search.status_code==200 and search.headers['cache-control']=='no-store'
    assert search.json()['activities']==[{'activity':'Ping','events':4}]
    r=client.post(url,json={"activities":["Ping"]},headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.json()['events_hidden']==4, r.text
    stale=client.post(url,json={"activities":[],"expected_sha256":"0"*64},headers=owner)
    assert stale.status_code==409 and client.get(url,headers=owner).json()['hidden'][0]['activity']=='Ping'
    assert client.post(url,json={"activities":[],"expected_sha256":"bad"},headers=owner).status_code==422
    s=client.get('/v1/event-logs/'+log+'/summary',headers=owner).json()
    assert s['events']==8 and s['hidden_activities']['activities']==['Ping'], s
    assert client.post(url,json={"activities":["Zed"]},headers=owner).status_code==422
    assert client.get(url,headers=owner).json()['hidden'][0]['activity']=='Ping'
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
