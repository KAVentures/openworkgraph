"""Trial domain packs without mutating the current log or metric library."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_domain_pack_preflight as preflight
from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from order_to_cash_fixture import MAPPING,NAMES,order_to_cash

PACK="order_to_cash"


@pytest.fixture(autouse=True)
def data(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)


def load():
    return workspace.import_log(BytesIO(json.dumps(order_to_cash()).encode()),
                                name="o2c",source_format="ocel")["log_id"]


def spec(**kwargs):
    return {"version":"1.0.0","mapping":deepcopy(MAPPING),**kwargs}


def test_trial_matches_installed_report_and_does_not_write():
    log=load()
    result=preflight.preview(log,PACK,spec(expected_installation=None))
    assert result["trial_completed"] and result["installable"]
    assert result["installation_performed"] is False
    assert packs.installed(log)["installs"]==[]
    assert query.list_metrics(log)["metrics"]==[]
    assert result["definition_changes"]["metrics"]=={"unchanged":0,"added":4,"removed":0}
    assert result["trial"]["metrics"][0]["cases"]>0
    saved=packs.install(log,PACK,spec(expected_installation=None,expected_preview=result["preview_token"]))
    assert saved["definitions_sha256"]==result["pack"]["definitions_sha256"]
    report=packs.report(log,PACK)
    for k in ("metrics","compliance","object_rules","object_model","not_applicable"):
        assert result["trial"][k]==report[k]


def test_reconfiguration_preview_preserves_current_install_and_discloses_delta():
    log=load()
    first=packs.install(log,PACK,spec())
    before=query.list_metrics(log)
    proposal=spec(parameters={"payment_terms_days":20},expected_installation=first["installation_token"])
    result=preflight.preview(log,PACK,proposal)
    assert result["definition_changes"]["metrics"]=={"unchanged":4,"added":0,"removed":0}
    assert result["definition_changes"]["compliance"]["removed"]==1
    assert result["definition_changes"]["compliance"]["added"]==1
    assert result["definition_changes"]["reference_model"]["changed"] is False
    assert query.list_metrics(log)==before
    assert packs.installed(log)["installs"][0]["installation_token"]==first["installation_token"]
    assert result["parameters"]["payment_terms_days"]==20
    changed=packs.install(log,PACK,{**proposal,"expected_preview":result["preview_token"]})
    assert changed["parameters"]["payment_terms_days"]==20


def test_collision_is_reported_without_overwriting_user_metric():
    log=load()
    query.save_metric(log,{"metric":{"name":"o2c_order_changes","kind":"activity_count","activity":NAMES["ship"]}})
    result=preflight.preview(log,PACK,spec())
    assert not result["installable"]
    assert result["metric_name_collisions"]==["o2c_order_changes"]
    assert result["installation_blockers"]==["metric_name_collision"]
    with pytest.raises(workspace.EventLogWorkspaceError,match="not this pack"):
        packs.install(log,PACK,spec(expected_preview=result["preview_token"]))
    assert len(query.list_metrics(log)["metrics"])==1


def test_changed_inputs_or_metric_library_invalidate_preview():
    log=load()
    result=preflight.preview(log,PACK,spec())
    with pytest.raises(packs.DomainPackConflict,match="preview changed"):
        packs.install(log,PACK,spec(parameters={"payment_terms_days":20},expected_preview=result["preview_token"]))
    query.save_metric(log,{"metric":{"name":"mine","kind":"activity_count","activity":NAMES["ship"]}})
    with pytest.raises(packs.DomainPackConflict,match="preview changed"):
        packs.install(log,PACK,spec(expected_preview=result["preview_token"]))
    assert packs.installed(log)["installs"]==[]


def test_preview_pin_changes_on_append(monkeypatch):
    log=load()
    monkeypatch.setattr(preflight,"MAX_TRIAL_EVENTS",0)
    result=preflight.preview(log,PACK,spec())
    workspace.append_log(log,BytesIO(json.dumps(order_to_cash()).encode()),source_format="ocel")
    with pytest.raises(packs.DomainPackConflict,match="preview changed"):
        packs.install(log,PACK,spec(expected_preview=result["preview_token"]))


def test_large_trial_discloses_setup_only(monkeypatch):
    log=load()
    monkeypatch.setattr(preflight,"MAX_TRIAL_EVENTS",1)
    result=preflight.preview(log,PACK,spec())
    assert result["trial"] is None and not result["trial_completed"]
    assert result["trial_unavailable_reason"]=="trial_population_bound"
    assert result["installable"] and result["role_counts"]["activities"]
    assert packs.installed(log)["installs"]==[]


def test_http_scope_and_strict_json(tmp_path):
    code=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_preflight import load,spec,PACK
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    log=load();base="/v1/event-logs/"+log+"/domain-packs/"+PACK
    assert c.post(base+"/preflight",json=spec(),headers=bearer).status_code in (401,403)
    r=c.post(base+"/preflight",json=spec(),headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["trial_completed"]
    assert c.post(base,json=spec(expected_preview="0"*64),headers=h).status_code==409
    assert c.post(base,json=spec(expected_preview=r.json()["preview_token"]),headers=h).status_code==200
    assert c.post(base+"/preflight",content=b'{"version":"1.0.0","version":"1.0.0"}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    result=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
