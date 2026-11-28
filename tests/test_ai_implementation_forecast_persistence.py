"""Integrated forecast review using the real persisted quality-benefit engine."""
from datetime import datetime, timezone, timedelta
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import automation_pipeline as pipeline
from server import automation_benefits as benefits
from server import automation_quality_benefit as quality
from server import ai_implementation_batch_routes as batch_routes
from server import ai_implementation_forecast_routes as routes
from shared import ai_implementation_forecast_review as forecast
from shared import ai_implementation_portfolio_link as link
from shared import ai_opportunity_handoff as handoff
from test_ai_implementation_portfolio_link import data

URL = '/v1/ai-opportunity-handoff/planning-forecast-review'
HEADERS = {'Authorization': 'OWG-Session owner'}


def test_recompute_persisted_periods_and_refuse_changed_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(batch_routes, 'dashboard_session_valid', lambda v: v == 'owner')
    args = data()
    sources, pin, proposals, review_pin, plan, planning_pin = args
    draft = {'sources':sources, 'proposal':proposals[0], 'title':'Reviewed candidate', 'description':''}
    preview = handoff.prepare(draft)
    idea, lineage = handoff.confirm({'draft':draft,'expected_preview_sha256':preview['preview_sha256'],'confirm_create_idea':True})
    saved = pipeline.create_from_ai_review(idea, lineage)
    key = saved['idea_id']
    saved = pipeline.transition(key, {'to':'assessing','expected_revision':saved['revision']})
    saved = pipeline.assess(key, {'ease':{k:5 for k in pipeline.EASE_QUESTIONS}, 'cases_per_month':100,
        'minutes_per_case':20,'eligible_share':0.8,'expected_revision':saved['revision']})
    saved = pipeline.transition(key, {'to':'approved','expected_revision':saved['revision']})
    saved = pipeline.transition(key, {'to':'implemented','expected_revision':saved['revision']})
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    benefits.record(key, {'period':'baseline','start':yesterday.isoformat(),'end':yesterday.isoformat(),
        'cases':100,'total_hours':20})
    benefits.record(key, {'period':'after','start':today.isoformat(),'end':today.isoformat(),
        'cases':100,'total_hours':10})
    inv = quality.inventory(key)
    periods = []
    for row in inv['measurements']:
        after = row['period'] == 'after'
        periods.append({'measurement_id':row['measurement_id'],'eligible_cases':80,
            'automation_attempted_cases':60 if after else 0,'automation_handoff_cases':10 if after else 0,
            'quality_checked_cases':50,'quality_failed_cases':5 if after else 2,'extra_review_hours':3 if after else 0,
            'review_effort_basis':'additional_to_retained_effort' if after else 'already_in_retained_effort',
            'nonlabor_cost':50 if after else 20})
    spec = {'snapshot_sha256':inv['snapshot_sha256'],'currency':'SEK','labor_cost_per_hour':100,'one_time_cost':150,
            'minimum_quality_coverage':0.5,'minimum_after_adoption':0.7,'maximum_quality_failure_rate_increase':0.03,
            'periods':periods}
    report = quality.analyze(key, spec)
    # Model the existing browser's JSON.stringify integer-valued float normalization.
    def browser_numbers(value):
        if type(value) is float and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {k:browser_numbers(v) for k,v in value.items()}
        if isinstance(value, list):
            return [browser_numbers(v) for v in value]
        return value
    req = {'sources':[json.dumps(s) for s in sources],'packet_sha256':pin,'proposals_json':json.dumps(proposals),
        'batch_review_sha256':review_pin,'plan_json':json.dumps(plan),'planning_review_sha256':planning_pin,
        'reconciliation_sha256':link.reconcile(*args,pipeline.pipeline()['ideas'])['reconciliation_sha256'],
        'item_index':0,'quality_report_json':json.dumps(browser_numbers(report)),
        'comparability':{**{k:True for k in forecast.BASES},'basis_note':'Synthetic same-scope fixture.'}}
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app)
    before = pipeline.get(key)
    r = client.post(URL,json=req,headers=HEADERS)
    assert r.status_code == 200,r.text
    result = json.loads(r.json()['report_json'])
    by_metric = {x['metric']:x for x in result['comparisons']}
    assert by_metric['monthly_case_volume']['reported_value'] == '3044'
    assert by_metric['monthly_net_human_hours']['status'] == 'above_forecast_range'
    assert result['quality_scenario_sha256'] == report['scenario_sha256']
    assert not result['all_declared_gates_met'] and not result['savings_claimed']
    assert pipeline.get(key) == before
    benefits.delete(key,periods[0]['measurement_id'])
    assert client.post(URL,json=req,headers=HEADERS).status_code == 422
