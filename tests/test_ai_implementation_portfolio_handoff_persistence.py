"""Integrated persistence regression for the actual pipeline planning operation."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import automation_pipeline as pipeline
from server import ai_implementation_batch_routes as batch_routes
from server import ai_implementation_portfolio_handoff_routes as routes
from shared import ai_implementation_portfolio_link as link
from shared import ai_opportunity_handoff as handoff
from test_ai_implementation_portfolio_link import data

BASE = '/v1/ai-opportunity-handoff/planning-save-'
HEADERS = {'Authorization': 'OWG-Session owner'}


@pytest.mark.parametrize('cycle', [False, True])
def test_real_saved_ideas_and_graph_checks(tmp_path, monkeypatch, cycle):
    monkeypatch.setattr(pipeline, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(batch_routes, 'dashboard_session_valid', lambda v: v == 'owner')
    args = data()
    sources, pin, proposals, review_pin, plan, planning_pin = args
    def create(index):
        draft = {'sources': sources, 'proposal': proposals[index], 'title': 'Reviewed candidate '+str(index), 'description': ''}
        preview = handoff.prepare(draft)
        idea, lineage = handoff.confirm({'draft':draft,'expected_preview_sha256':preview['preview_sha256'],'confirm_create_idea':True})
        return pipeline.create_from_ai_review(idea, lineage)
    dependency, target = create(0), create(2)
    external = pipeline.create({'title':'Existing unrelated dependency'})
    target = pipeline.plan(target['idea_id'], {'owner':'Owner','team':'Team','target_quarter':'2027-Q1',
        'budget':None,'depends_on':[external['idea_id']],'expected_revision':target['revision']})
    if cycle:
        pipeline.plan(dependency['idea_id'], {'depends_on':[target['idea_id']],'expected_revision':dependency['revision']})
    before = pipeline.get(target['idea_id'])
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app)
    req = {'sources':[json.dumps(s) for s in sources],'packet_sha256':pin,'proposals_json':json.dumps(proposals),
        'batch_review_sha256':review_pin,'plan_json':json.dumps(plan),'planning_review_sha256':planning_pin,
        'reconciliation_sha256':link.reconcile(*args,pipeline.pipeline()['ideas'])['reconciliation_sha256'],
        'item_index':2,'budget_estimate':'150.25'}
    r = client.post(BASE+'preview',json=req,headers=HEADERS)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert pipeline.get(target['idea_id']) == before
    r = client.post(BASE+'confirm',json={**req,'expected_preview_sha256':preview['preview_sha256'],
        'confirm_save_planning':True},headers=HEADERS)
    after = pipeline.get(target['idea_id'])
    if cycle:
        assert r.status_code == 422 and after == before
    else:
        assert r.status_code == 200, r.text
        assert after['revision'] == before['revision']+1 and after['state'] == before['state']
        assert after['assessment'] == before['assessment'] and after['execution_authorized'] is False
        assert after['planning']['owner'] == 'Owner' and after['planning']['team'] == 'Team'
        assert after['planning']['target_quarter'] == '2027-Q1'
        assert after['planning']['budget'] == {'amount':150.25,'currency':'SEK','basis':'estimate'}
        assert set(after['planning']['depends_on']) == {dependency['idea_id'],external['idea_id']}
        assert after['history'][-1]['action'] == 'planning_saved'
        assert client.post(BASE+'confirm',json={**req,'expected_preview_sha256':preview['preview_sha256'],
            'confirm_save_planning':True},headers=HEADERS).status_code == 422
        assert pipeline.get(target['idea_id']) == after
