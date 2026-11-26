from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_object_constraints as constraints
from server import event_log_object_rule_sets as sets
from server import event_log_workspace as workspace
from test_event_log_objects import _ocel, _order_to_cash

RULE = {"template": "event_cardinality", "activity": "Pay", "object_type": "order", "min": 2}
SUPPLIED = {"template": "supplied_object_cardinality", "object_type": "order", "related_type": "item",
            "qualifier": " ", "direction": "both", "min": 1}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_saved_rules_reload_and_check_without_losing_literal_fields():
    log = _ocel(_order_to_cash())
    saved = sets.save(log, {"name": "  Pay and links  ", "rules": [RULE, SUPPLIED]})
    selected = sets.get(log, saved["set_id"])
    assert selected == saved and saved["name"] == "Pay and links" and saved["revision"] == 1
    assert saved["rules"][1]["qualifier"] == " " and saved["rules"][1]["direction"] == "both"
    assert sets.listing(log)["sets"] == [{k: saved[k] for k in ("set_id", "name", "revision", "updated_at")} | {"rule_count": 2}]
    assert constraints.check(log, {"rules": selected["rules"]})["results"][0]["violating"] == 2
    updated = sets.update(log, saved["set_id"], {"name": "Renamed", "rules": [RULE], "expected_revision": 1})
    assert updated["created_at"] == saved["created_at"] and updated["revision"] == 2 and updated["name"] == "Renamed"
    with pytest.raises(workspace.EventLogWorkspaceError, match="has changed"):
        sets.delete(log, saved["set_id"], 1)
    assert sets.delete(log, saved["set_id"], 2) == {"deleted": saved["set_id"]}
    assert sets.listing(log)["sets"] == []


def test_preview_portable_rules_checks_literal_labels_without_saving():
    log = _ocel(_order_to_cash())
    packet = {"format": sets.TEMPLATE_FORMAT, "definition": {
        "name": " Transferable ", "rules": [RULE, SUPPLIED, {**RULE, "activity": " Pay ", "object_type": "missing"}]
    }}
    preview = sets.preview_template(log, packet)
    assert preview["definition"]["name"] == "Transferable"
    assert preview["definition"]["rules"][1]["qualifier"] == " "
    assert preview["label_checks"][0]["missing_activities"] == []
    assert preview["label_checks"][1]["missing_qualifiers"] == [" "]
    assert preview["label_checks"][2]["missing_activities"] == [" Pay "]
    assert preview["label_checks"][2]["missing_object_types"] == ["missing"]
    assert sets.listing(log)["sets"] == []
    assert sets.save(log, preview["definition"])["rules"] == preview["definition"]["rules"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="rule set file"):
        sets.preview_template(log, {**packet, "case_id": "do not include"})
    with pytest.raises(workspace.EventLogWorkspaceError):
        sets.preview_template(log, {"format": sets.TEMPLATE_FORMAT, "definition": {**packet["definition"], "results": []}})


def test_set_names_are_scoped_and_deleting_a_log_removes_its_sets():
    log, other = _ocel(_order_to_cash()), _ocel(_order_to_cash())
    spec = {"name": "Rules", "rules": [RULE]}
    saved = sets.save(log, spec)
    kept = sets.save(other, spec)
    with pytest.raises(workspace.EventLogWorkspaceError, match="already exists"):
        sets.save(log, spec)
    sibling = sets.save(log, {**spec, "name": "Sibling"})
    with pytest.raises(workspace.EventLogWorkspaceError, match="already exists"):
        sets.update(log, sibling["set_id"], {**spec, "expected_revision": 1})
    with pytest.raises(workspace.EventLogWorkspaceError, match="no saved"):
        sets.get(other, saved["set_id"])
    workspace.delete_log(log)
    assert sets.get(other, kept["set_id"]) == kept
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM object_rule_sets WHERE log_id = ?", [log]).fetchone()[0] == 0
    finally:
        conn.close()


def test_revision_check_prevents_lost_updates_and_validates_edited_rules():
    log = _ocel(_order_to_cash())
    saved = sets.save(log, {"name": "Rules", "rules": [RULE]})

    def update(name):
        try:
            return sets.update(log, saved["set_id"], {"name": name, "rules": [RULE], "expected_revision": 1})
        except workspace.EventLogWorkspaceError as error:
            assert "has changed" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ("First", "Second")))
    winner, = (value for value in results if value is not None)
    assert winner["revision"] == 2 and sets.get(log, saved["set_id"]) == winner
    with pytest.raises(workspace.EventLogWorkspaceError, match="max must not"):
        sets.update(log, saved["set_id"], {"name": "Rules", "rules": [{**RULE, "max": 1}], "expected_revision": 2})
    assert sets.get(log, saved["set_id"]) == winner


def test_dashboard_routes_and_strict_settings(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    r = client.post("/v1/event-logs/import?format=csv&name=Rules&case_column=case&activity_column=activity&timestamp_column=time",
                    headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"},
                    content=b"case,activity,time\nc,A,2024-01-01T00:00:00Z")
    base = "/v1/event-logs/" + r.json()["log_id"] + "/object-rule-sets"
    spec = {"name": "Review", "rules": [{"template": "event_cardinality", "activity": "A", "object_type": "case", "min": 1}]}
    assert client.get(base, headers=bearer).status_code == 403
    assert client.post(base, json=spec, headers=bearer).status_code == 403
    template = {"format": "openworkgraph.object-rule-set.v1", "definition": spec}
    path = base + "/template/preview"
    assert client.post(path, json=template, headers=bearer).status_code == 403
    preview = client.post(path, json=template, headers=owner)
    assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store", preview.text
    assert preview.json()["label_checks"][0]["missing_activities"] == []
    assert client.get(base, headers=owner).json() == {"sets": []}

    response = client.post(base, json=spec, headers=owner)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store", response.text
    key = base + "/" + response.json()["set_id"]
    assert client.get(key, headers=owner).json()["rules"][0]["activity"] == "A"
    assert client.put(key, json={**spec, "expected_revision": True}, headers=owner).status_code == 422
    assert client.put(key, json={**spec, "expected_revision": 1}, headers=owner).json()["revision"] == 2
    assert client.delete(key + "?expected_revision=2", headers=bearer).status_code == 403
    assert client.delete(key + "?expected_revision=2", headers=owner).status_code == 200
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "route-data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"), "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
