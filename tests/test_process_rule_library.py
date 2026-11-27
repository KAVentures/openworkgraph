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


def _record(model):
    return {"workflow_id": "p", "revision": 3, "content_sha256": "c"*64,
            "process_model": model}


def _selection(row):
    return {"rule_id": row["rule_id"], "library_revision": row["latest_revision"],
            "content_sha256": row["latest_content_sha256"]}


def test_atomic_upgrade_preview_and_transitive_table_invalidation(library):
    from copy import deepcopy
    from test_process_decision_dependencies import graph as dependency_graph
    model = ProcessModel.model_validate(dependency_graph()).model_dump()
    source_rules = [r for r in model["business_rules"] if r["id"] in {"score_high", "score_low"}]
    first = library.save({"name": "Credit rules", "rules": source_rules})
    sid = first["rule_set_id"]
    # Build a fully reviewed fixture with exact historical rule pins.
    for r in model["business_rules"]:
        source = next((s for s in first["rules"] if s["id"] == r["id"]), None)
        if source:
            r["library_ref"] = {"rule_set_id": sid, "revision": 1,
                                "rule_id": r["id"], "content_sha256": source["content_sha256"]}
    original = deepcopy(model)
    updated = deepcopy(source_rules)
    for r in updated:
        r["predicate"]["conditions"][0]["expected"] = 750
        r["text"] += " revised"
    library.save({"rule_set_id": sid, "expected_revision": 1, "name": "Credit rules", "rules": updated})
    preview = library.upgrade_review(_record(model))
    assert preview["selectable_rule_count"] == 2
    assert preview["proposed_process_model"] is None
    assert all(r["text_changed"] and r["predicate_changed"] for r in preview["linked_rules"])
    assert all(r["direct_decision_table_ids"] == ["credit_table"] for r in preview["linked_rules"])
    selected = [_selection(r) for r in preview["linked_rules"]]
    result = library.upgrade_review(_record(model), selected)
    assert result["selected_rule_ids"] == ["score_high", "score_low"]
    assert result["cleared_rule_confirmations"] == ["score_high", "score_low"]
    assert result["cleared_decision_table_confirmations"] == ["approval_table", "credit_table"]
    proposal = result["proposed_process_model"]
    assert all(not t["user_confirmed"] for t in proposal["decision_tables"])
    assert all(r["library_ref"]["revision"] == 2 and not r["user_confirmed"]
               for r in proposal["business_rules"] if r["id"] in result["selected_rule_ids"])
    assert all(r["user_confirmed"] for r in proposal["business_rules"] if r["id"].startswith("credit_"))
    assert model == original and not result["process_model_saved"] and not result["execution_authorized"]
    assert library.get(sid, 1)["rules"][0]["predicate"]["conditions"][0]["expected"] == 700
    # A new source revision invalidates the entire selection.
    library.save({"rule_set_id": sid, "expected_revision": 2, "name": "Credit rules", "rules": updated})
    with pytest.raises(library.RuleLibraryError, match="changed"):
        library.upgrade_review(_record(model), selected)
    assert model == original


def test_upgrade_refuses_edited_forged_removed_or_duplicate_links(library):
    from copy import deepcopy
    first = library.save({"name": "Purchasing", "rules": [LIMIT]})
    sid = first["rule_set_id"]
    model = library.link(graph(), "small", sid, "limit")
    library.save({"rule_set_id": sid, "expected_revision": 1, "name": "Purchasing", "rules": [LIMIT]})
    preview = library.upgrade_review(_record(model))
    [row] = preview["linked_rules"]
    assert row["status"] == "newer_revision_same_content"
    selection = _selection(row)
    assert library.upgrade_review(_record(model), [selection])["selected_rule_ids"] == ["small"]
    with pytest.raises(library.RuleLibraryError, match="duplicated"):
        library.upgrade_review(_record(model), [selection, selection])
    for key, value, expected in [
        ("revision", 199, "pinned_rule_missing"),
        ("content_sha256", "f"*64, "pinned_digest_mismatch"),
    ]:
        bad = deepcopy(model)
        bad["business_rules"][0]["library_ref"][key] = value
        row = library.upgrade_review(_record(bad))["linked_rules"][0]
        assert row["status"] == expected and not row["selectable"]
        assert library.drift([_record(bad)])["findings"][0]["status"] == expected
        with pytest.raises(library.RuleLibraryError):
            library.upgrade_review(_record(bad), [selection])
    edited = deepcopy(model)
    edited["business_rules"][0]["text"] = "Locally edited"
    assert library.upgrade_review(_record(edited))["linked_rules"][0]["status"] == "edited_in_design"
    library.save({"rule_set_id": sid, "expected_revision": 2, "name": "Purchasing",
                  "rules": [dict(LIMIT, id="other")]})
    assert library.upgrade_review(_record(model))["linked_rules"][0]["status"] == "removed_from_library"


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
    latest = catalog.get(wid)
    upgrade = f"/v1/process-catalog/rule-library/upgrade-review?workflow_id={wid}&revision={latest['revision']}"
    base = {"expected_content_sha256": latest["content_sha256"]}
    assert client.post(upgrade, json=base, headers=bearer).status_code == 403
    assert client.post(upgrade, json={"expected_content_sha256": "0"*64}, headers=owner).status_code == 409
    preview = client.post(upgrade, json=base, headers=owner)
    assert preview.status_code == 200, preview.text
    [row] = preview.json()["linked_rules"]
    selected = {"rule_id": row["rule_id"], "library_revision": row["latest_revision"],
                "content_sha256": row["latest_content_sha256"]}
    proposed = client.post(upgrade, json={**base, "selections": [selected]}, headers=owner)
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["proposed_process_model"]["business_rules"][0]["library_ref"]["revision"] == 2
    assert catalog.get(wid)["revision"] == latest["revision"]
    assert client.post(upgrade, json={**base, "selections": [{**selected, "library_revision": 1}]}, headers=owner).status_code == 409
    stale_process = f"/v1/process-catalog/rule-library/upgrade-review?workflow_id={wid}&revision={rev}"
    assert client.post(stale_process, json=base, headers=owner).status_code == 409
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
