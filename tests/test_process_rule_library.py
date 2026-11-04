"""Reusable, versioned business-rule library (#370 R21)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared.process_model import ProcessModel


def graph():
    return {'goal': 'Review an authorized purchase', 'baseline_source': 'user_declared',
            'baseline_path': ['Review', 'Accept'], 'baseline_coverage': 'user_declared_design',
            'activities': [{'id': 'choose', 'label': 'Review', 'kind': 'decision'},
                           {'id': 'accept', 'label': 'Accept'}, {'id': 'reject', 'label': 'Reject'}],
            'transitions': [{'source': 'choose', 'target': 'accept', 'rule_id': 'small'},
                            {'source': 'choose', 'target': 'reject', 'rule_id': 'large'}],
            'entry_activity_id': 'choose', 'terminal_activity_ids': ['accept', 'reject'],
            'business_rules': [{'id': 'small', 'text': 'Small purchase', 'user_confirmed': True},
                               {'id': 'large', 'text': 'Large purchase', 'user_confirmed': True}]}


LIMIT = {'id': 'limit', 'text': 'Purchases under 100 need no approval',
         'predicate': {'conditions': [{'path': '/amount', 'operator': 'lt', 'expected': 100}]}}


@pytest.fixture
def library(tmp_path, monkeypatch):
    from server import process_rule_library as lib
    monkeypatch.setattr(lib, 'DATA_DIR', tmp_path)
    return lib


def test_unlinked_rules_keep_their_serialized_form():
    dumped = ProcessModel.model_validate(graph()).model_dump()
    assert all('library_ref' not in r for r in dumped['business_rules'])


def test_saves_are_versioned_and_guarded(library, tmp_path):
    first = library.save({'name': 'Purchasing', 'source_label': 'Policy v1', 'rules': [LIMIT]})
    sid = first['rule_set_id']
    assert first['revision'] == 1 and len(first['rules'][0]['content_sha256']) == 64
    changed = dict(LIMIT, text='Purchases under 50 need no approval')
    second = library.save({'rule_set_id': sid, 'expected_revision': 1, 'name': 'Purchasing', 'rules': [changed]})
    assert second['revision'] == 2 and library.get(sid, 1)['rules'][0]['text'] == LIMIT['text']
    with pytest.raises(library.RuleLibraryError, match='changed'):
        library.save({'rule_set_id': sid, 'expected_revision': 1, 'name': 'Purchasing', 'rules': [LIMIT]})
    with pytest.raises(library.RuleLibraryError, match='unique'):
        library.save({'name': 'Dup', 'rules': [LIMIT, LIMIT]})
    with pytest.raises(library.RuleLibraryError):
        library.save({'name': 'Bad', 'rules': [{'id': 'x', 'text': ''}]})
    assert library.listing()['rule_sets'][0]['revision'] == 2
    if os.name == 'posix':
        assert (tmp_path / 'process_rule_library.json').stat().st_mode & 0o777 == 0o600


def test_link_proposes_an_unconfirmed_copy(library):
    sid = library.save({'name': 'Purchasing', 'rules': [LIMIT]})['rule_set_id']
    model = library.link(graph(), 'small', sid, 'limit')
    rule = next(r for r in model['business_rules'] if r['id'] == 'small')
    assert rule['text'] == LIMIT['text'] and rule['user_confirmed'] is False
    assert rule['predicate'] == ProcessModel.model_validate(model).business_rules[0].predicate.model_dump()
    assert rule['library_ref']['rule_set_id'] == sid and rule['library_ref']['revision'] == 1
    with pytest.raises(library.RuleLibraryError):
        library.link(graph(), 'missing', sid, 'limit')
    with pytest.raises(library.RuleLibraryError):
        library.link(graph(), 'small', sid, 'nope')


def test_drift_reports_each_status(library):
    sid = library.save({'name': 'Purchasing', 'rules': [LIMIT, dict(LIMIT, id='other', text='Other rule', predicate=None)]})['rule_set_id']
    model = library.link(graph(), 'small', sid, 'limit')
    model = library.link(model, 'large', sid, 'other')
    current = {'workflow_id': 'p', 'revision': 1, 'process_model': model}
    assert library.drift([current])['findings'] == []
    edited = json.loads(json.dumps(current))
    edited['process_model']['business_rules'][0]['text'] = 'Locally edited'
    assert [f['status'] for f in library.drift([edited])['findings']] == ['edited_in_design']
    library.save({'rule_set_id': sid, 'expected_revision': 1, 'name': 'Purchasing',
                  'rules': [dict(LIMIT, text='Purchases under 50 need no approval')]})
    statuses = sorted(f['status'] for f in library.drift([current])['findings'])
    assert statuses == ['library_rule_changed', 'removed_from_library']
    gone = json.loads(json.dumps(current))
    gone['process_model']['business_rules'][0]['library_ref']['rule_set_id'] = 'rs_' + '0' * 16
    assert 'rule_set_missing' in {f['status'] for f in library.drift([gone])['findings']}
    assert library.drift([current])['evaluates_rules'] is False


def test_rule_library_routes(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.local_process_catalog import catalog
from shared.process_catalog import CatalogWrite
saved = catalog.save(CatalogWrite(title="Purchase", process_model=json.loads(MODEL), user_confirmed=True))
wid, rev = saved["workflow_id"], saved["revision"]
owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session(), "Content-Type": "application/json"}
bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token(), "Content-Type": "application/json"}
rule = {"id": "limit", "text": "Purchases under 100 need no approval"}
with TestClient(app) as client:
    body = json.dumps({"name": "Purchasing", "rules": [rule]})
    assert client.post("/v1/process-catalog/rule-library", content=body, headers=bearer).status_code == 403
    assert client.get("/v1/process-catalog/rule-library", headers=bearer).status_code == 403
    made = client.post("/v1/process-catalog/rule-library", content=body, headers=owner)
    assert made.status_code == 200, made.text
    sid = made.json()["rule_set_id"]
    assert client.get("/v1/process-catalog/rule-library", headers=owner).json()["rule_sets"][0]["rule_set_id"] == sid
    assert client.get(f"/v1/process-catalog/rule-library/set?rule_set_id={sid}&revision=1", headers=owner).status_code == 200
    stale = json.dumps({"rule_set_id": sid, "expected_revision": 0, "name": "x", "rules": [rule]})
    assert client.post("/v1/process-catalog/rule-library", content=stale, headers=owner).status_code == 409
    link = json.dumps({"rule_id": "small", "rule_set_id": sid, "library_rule_id": "limit"})
    q = f"?workflow_id={wid}&revision={rev}"
    assert client.post("/v1/process-catalog/rule-library/link" + q, content=link, headers=bearer).status_code == 403
    proposal = client.post("/v1/process-catalog/rule-library/link" + q, content=link, headers=owner)
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["process_model_saved"] is False and catalog.get(wid)["revision"] == rev
    model = proposal.json()["proposed_process_model"]
    catalog.save(CatalogWrite(workflow_id=wid, expected_revision=rev, title="Purchase", process_model=model, user_confirmed=True))
    assert client.get("/v1/process-catalog/rule-library/drift", headers=owner).json()["findings"] == []
    changed = json.dumps({"rule_set_id": sid, "expected_revision": 1, "name": "Purchasing",
                          "rules": [dict(rule, text="Purchases under 50 need no approval")]})
    assert client.post("/v1/process-catalog/rule-library", content=changed, headers=owner).status_code == 200
    drift = client.get("/v1/process-catalog/rule-library/drift", headers=owner).json()
    assert [f["status"] for f in drift["findings"]] == ["library_rule_changed"], drift
    bad = json.dumps({"rule_id": "small", "rule_set_id": sid, "library_rule_id": "limit", "extra": 1})
    assert client.post("/v1/process-catalog/rule-library/link" + q, content=bad, headers=owner).status_code == 422
'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path / "data"), WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path / "auth"),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path / "config.json"))
    script = f"MODEL = {json.dumps(json.dumps(graph()))}\n" + code
    result = subprocess.run([sys.executable, "-c", script], env=env, cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_catalog_ui_offers_the_rule_library():
    script = (Path(__file__).resolve().parents[1] / "dashboard" / "process_catalog.js").read_text(encoding="utf-8")
    assert "renderRuleLibrary(host)" in script and "/v1/process-catalog/rule-library/link" in script
    assert "/v1/process-catalog/rule-library/drift" in script
