"""Portable domain pack setup: exact catalog/version, no source data or writes."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_domain_pack_template as templates
from server import event_log_domain_pack_preflight as preflight
from server import event_log_domain_packs as packs
from server import event_log_query as query
from server import event_log_workspace as workspace
from order_to_cash_fixture import MAPPING, NAMES, order_to_cash

PACK = "order_to_cash"


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def load(rename=None):
    doc = order_to_cash()
    for entry in doc["eventTypes"]:
        entry["name"] = (rename or {}).get(entry["name"], entry["name"])
    for event in doc["events"]:
        event["type"] = (rename or {}).get(event["type"], event["type"])
    return workspace.import_log(BytesIO(json.dumps(doc).encode()), name="o2c", source_format="ocel")["log_id"]


def installed_template():
    source = load()
    saved = packs.install(source, PACK, {"version": "1.0.0", "mapping": deepcopy(MAPPING),
                                         "parameters": {"payment_terms_days": 20}})
    return source, saved, templates.export_template(source, PACK)


def test_portable_roundtrip_is_configuration_only_and_fresh_preflight():
    source, saved, template = installed_template()
    target = load()
    assert source != target
    assert set(template) == templates.FIELDS
    raw = json.dumps(template)
    assert source not in raw and saved["installation_token"] not in raw
    assert all(mid not in raw for mid in saved["metric_ids"])
    assert "events" not in template and "report" not in template
    assert templates.export_template(source, PACK) == template
    review = templates.review_template(target, PACK, json.loads(raw))
    assert review["suggested_mapping"] == MAPPING
    assert review["missing_required_roles"] == []
    assert review["parameters"]["payment_terms_days"] == 20
    assert review["semantic_mapping_verified"] is False
    assert review["installation_performed"] is False
    assert packs.installed(target)["installs"] == [] and query.list_metrics(target)["metrics"] == []
    assert next(r for r in review["roles"] if r["role"] == "create_order")["destination_count"] == 20
    proposed = {"version": review["version"], "mapping": review["suggested_mapping"],
                "parameters": review["parameters"], "expected_installation": None}
    trial = preflight.preview(target, PACK, proposed)
    assert trial["installable"]
    result = packs.install(target, PACK, {**proposed, "expected_preview": trial["preview_token"]})
    assert result["definitions_sha256"] == saved["definitions_sha256"]


def test_missing_names_and_optional_omissions_are_separate():
    source = load()
    mapping = deepcopy(MAPPING)
    del mapping["activities"]["change_order"]
    packs.install(source, PACK, {"version": "1.0.0", "mapping": mapping})
    target = load({NAMES["ship"]: "new shipping", NAMES["approve_credit"]: "new credit"})
    result = templates.review_template(target, PACK, templates.export_template(source, PACK))
    assert result["missing_required_roles"] == ["ship"]
    assert result["missing_optional_mapped_roles"] == ["approve_credit"]
    rows = {r["role"]: r for r in result["roles"]}
    assert rows["change_order"]["status"] == "not_mapped_in_template"
    assert rows["ship"]["status"] == "label_not_found"
    assert rows["ship"]["destination_count"] == 0
    assert "ship" not in result["suggested_mapping"]["activities"]
    assert "approve_credit" not in result["suggested_mapping"]["activities"]
    assert packs.installed(target)["installs"] == []


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(extra="not allowed"),
    lambda v: v.update(pack_id="service_management"),
    lambda v: v.update(template_sha256="0" * 64),
    lambda v: v.update(pack_sha256="0" * 64),
    lambda v: v["parameters"].update(payment_terms_days=25),
    lambda v: v["parameters"].update(payment_terms_days=True),
    lambda v: v["parameters"].pop("invoice_within_days"),
    lambda v: v["mapping"]["activities"].update(ship="another"),
    lambda v: v["mapping"]["activities"].update(ship=123),
])
def test_reject_changed_or_invalid_templates_without_writes(mutate):
    source, _, template = installed_template()
    target = load()
    mutate(template)
    with pytest.raises(workspace.EventLogWorkspaceError):
        templates.review_template(target, PACK, template)
    assert packs.installed(target)["installs"] == []
    assert query.list_metrics(target)["metrics"] == []


def test_refuse_changed_catalog_even_with_same_version(monkeypatch):
    source, _, template = installed_template()
    changed = deepcopy(packs.ORDER_TO_CASH_1_0_0)
    changed["assumptions"].append("Changed local catalog")
    monkeypatch.setitem(packs.PACKS, PACK, {"1.0.0": changed})
    with pytest.raises(workspace.EventLogWorkspaceError, match="catalog definition"):
        templates.review_template(load(), PACK, template)


def test_refuse_old_version_without_implicit_upgrade(monkeypatch):
    source, _, template = installed_template()
    newer = deepcopy(packs.ORDER_TO_CASH_1_0_0)
    newer["version"] = "1.1.0"
    monkeypatch.setitem(packs.PACKS, PACK, {"1.0.0": packs.ORDER_TO_CASH_1_0_0, "1.1.0": newer})
    with pytest.raises(workspace.EventLogWorkspaceError, match="not the latest"):
        templates.review_template(load(), PACK, template)


def test_http_scope_and_strict_json(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_domain_pack_template import installed_template, load, PACK
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    source,_,template=installed_template()
    target=load()
    a="/v1/event-logs/"+source+"/domain-packs/"+PACK+"/template"
    b="/v1/event-logs/"+target+"/domain-packs/"+PACK+"/template/review"
    assert c.get(a,headers=bearer).status_code in (401,403)
    assert c.post(b,json=template,headers=bearer).status_code in (401,403)
    r=c.get(a,headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store" and r.json()==template
    r=c.post(b,json=template,headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    assert r.json()["installation_performed"] is False
    assert c.post(b,content=b'{"version":"1.0.0","version":"1.0.0"}',headers={**h,"Content-Type":"application/json"}).status_code==422
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    result=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code],
        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
