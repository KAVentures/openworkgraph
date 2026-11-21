"""Similar activity labels as mapping suggestions."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_activity_mappings as mappings
from server import event_log_label_suggestions as ls
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(labels_with_counts):
    rows = ["case,activity,time"]
    k = 0
    for label, n in labels_with_counts:
        for _ in range(n):
            rows.append(f'c{k},"{label}",2024-01-01T{k % 24:02d}:{k // 24 % 60:02d}:00Z')
            k += 1
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="l", source_format="csv", mapping=MAPPING)["log_id"]


def test_groups_by_normalization_and_spelling():
    log = _import([("Approve invoice", 10), ("approve  invoice", 3), ("Approve Invoice.", 2),
                   ("Recieve goods", 2), ("Receive goods", 8),
                   ("Step 1", 5), ("Step 2", 5), ("Pay", 4), ("pay", 1)])
    r = ls.suggestions(log)
    groups = {g["suggested_label"]: g for g in r["groups"]}
    approve = groups["Approve invoice"]
    assert [l["activity"] for l in approve["labels"]] == ["Approve invoice", "approve  invoice", "Approve Invoice."]
    assert approve["reason"] == "same apart from case, spacing or punctuation" and approve["events"] == 15
    receive = groups["Receive goods"]
    assert [l["activity"] for l in receive["labels"]] == ["Receive goods", "Recieve goods"]
    assert receive["reason"] == "similar spelling"
    assert "Step 1" not in groups and "Step 2" not in groups   # different digits: different steps
    assert groups["Pay"]["labels"] == [{"activity": "Pay", "events": 4}, {"activity": "pay", "events": 1}]
    assert r["groups"][0]["suggested_label"] == "Approve invoice"   # most events first
    assert r["groups_total"] == 3


def test_suggestions_can_become_a_mapping_and_no_false_groups():
    log = _import([("Approve invoice", 3), ("APPROVE INVOICE", 2), ("Close", 4), ("Open", 4)])
    groups = ls.suggestions(log)["groups"]
    assert len(groups) == 1
    made = mappings.create(log, {"name": "Similar labels", "collapse_repeats": False,
                                 "groups": [{"label": g["suggested_label"],
                                             "activities": [l["activity"] for l in g["labels"]]} for g in groups]})
    assert made["activities_not_in_log"] == []
    clean = _import([("Open", 2), ("Close", 2), ("Ship", 2)])
    assert ls.suggestions(clean)["groups"] == []


def test_owner_only_route(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = "case,activity,time\nc1,Approve,2024-01-01T00:00:00Z\nc2,approve,2024-01-01T01:00:00Z\nc3,Close,2024-01-01T02:00:00Z\n"
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=doc.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    url='/v1/event-logs/'+made.json()['log_id']+'/activity-label-suggestions'
    assert client.get(url).status_code in (401,403) and client.get(url,headers=bearer).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert [l['activity'] for l in r.json()['groups'][0]['labels']]==['Approve','approve']
    assert client.get('/v1/event-logs/log_0000000000000000/activity-label-suggestions',headers=owner).status_code in (404,422)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
