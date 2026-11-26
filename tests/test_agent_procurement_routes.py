"""Production local procurement endpoints: exact owner-scoped revision, no retention."""
import os
from pathlib import Path
import subprocess
import sys


def test_procurement_dashboard_auth_revision_and_strict_documents(tmp_path):
    code=r"""
import json
from copy import deepcopy
from pathlib import Path
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,db
from test_process_shadow_review import generated_report,model
from shared.agent_procurement import digest
with TestClient(app) as client:
    h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    saved=client.post('/v1/process-catalog',headers=h,json={'title':'Procurement review','process_model':model(),'user_confirmed':True}).json()
    params={'workflow_id':saved['workflow_id'],'revision':1}
    packet=client.get('/v1/process-catalog/export',headers=h,params=params).json()
    report=generated_report(packet,Path(__import__('sys').argv[1])/'project')
    alternate=deepcopy(report);alternate['results_sha256']=digest('other capture')
    def candidate(cid,r):
        return {'candidate_id':cid,'provider':'provider','model':cid,'runtime':'cli','report':r,
                'measurements':[{'case_id':'case_1','cost_microunits':100,'elapsed_ms':500}]}
    comparison={'format':'openworkgraph.agent-procurement.v1','baseline_candidate_id':'current','currency':'USD',
                'gates':{'minimum_match_basis_points':0,'maximum_critical_cases':0,'require_all_categories':False},
                'candidates':[candidate('current',report),candidate('other',alternate)]}
    base='/v1/process-catalog/procurement/'
    body={'document':json.dumps(comparison)}
    assert client.post(base+'compare',params=params,json=body).status_code==401
    producer_path='/v1/process-catalog/procurement-producer/ingest'
    assert client.post(producer_path,json={}).status_code==401
    assert client.post(producer_path,json={},headers=h).status_code==401
    assert client.post(producer_path,json={},headers=bearer).status_code==401
    assert client.post(producer_path,json={},headers={**bearer,'Origin':'http://localhost'}).status_code==403
    assert client.post(base+'router-producer-grant',params=params,json={'document':'{}'},headers=bearer).status_code==403
    assert client.post(base+'router-producer-revoke',params=params,json={'document':'{}'},headers=bearer).status_code==403
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers=bearer).status_code==403
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers={**h,'X-OpenWorkGraph-Context':'ai'}).status_code==403
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers=h).status_code==422
    for operation in ("router-corroboration-require","router-corroboration-manifest","router-corroboration-record","router-source-outcome-bind","router-source-outcome-check"):
        assert client.post(base+operation,params=params,json={"document":"{}"},headers=bearer).status_code==403
        assert client.post(base+operation,params=params,json={"document":"{}"},headers={**h,"X-OpenWorkGraph-Context":"ai"}).status_code==403
        assert client.post(base+operation,params=params,json={"document":"{}"},headers=h).status_code==422
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers=bearer).status_code==403
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers={**h,'X-OpenWorkGraph-Context':'ai'}).status_code==403
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers=h).status_code==422
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'}).status_code==401
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers=bearer).status_code==403
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers={**h,'X-OpenWorkGraph-Context':'ai'}).status_code==403
    assert client.post(base+'router-producer-manifest',params=params,json={'document':'{}'},headers=h).status_code==422
    assert client.post(base+'router-producer-grant',params=params,json={'document':'{}'},headers={**h,'X-OpenWorkGraph-Context':'ai'}).status_code==403
    assert client.post(base+'router-journal-refresh',params=params,json={'document':'{}'},headers=bearer).status_code==403
    allocation_path='/v1/process-catalog/procurement-workforce/allocation'
    assert client.post(allocation_path,json={}).status_code==401
    assert client.post(allocation_path,json={},headers=bearer).status_code==403
    assert client.post(allocation_path,json={},headers=h).status_code==422
    exchange_path='/v1/process-catalog/procurement-workforce/exchange/export'
    assert client.post(exchange_path,json={}).status_code==401
    assert client.post(exchange_path,json={},headers=bearer).status_code==403
    assert client.post(exchange_path,json={},headers=h).status_code==422
    signing_path='/v1/process-catalog/procurement-workforce/exchange/identity'
    assert client.post(signing_path,json={}).status_code==401
    assert client.post(signing_path,json={},headers=bearer).status_code==403
    assert client.post(signing_path,json={'document':'{"confirmed":false}'},headers=h).status_code==422
    trust_path='/v1/process-catalog/procurement-workforce/exchange/trust-status'
    assert client.post(trust_path,json={'document':'{}'}).status_code==401
    assert client.post(trust_path,json={'document':'{}'},headers=bearer).status_code==403
    trust_status=client.post(trust_path,json={'document':'{}'},headers=h)
    assert trust_status.status_code==200,trust_status.text
    assert trust_status.json()['revision']==0
    assert trust_status.headers['cache-control']=='no-store'
    overview_path='/v1/process-catalog/procurement-workforce/overview'
    assert client.get(overview_path).status_code==401
    assert client.get(overview_path,headers=bearer).status_code==403
    overview_result=client.get(overview_path,headers=h)
    assert overview_result.status_code==200,overview_result.text
    assert overview_result.headers['cache-control']=='no-store'
    assert overview_result.json()['journal_count']==0
    assert client.post(base+'router-finance-reconcile',params=params,json={'document':'{}'},headers=bearer).status_code==403
    assert client.post(base+'router-finance-reconcile',params=params,json={'document':'{}'},headers=h).status_code==422
    assert client.post(base+'router-journal-schedule',params=params,json={'document':'{}'},headers=bearer).status_code==403
    assert client.post(base+'router-journal-schedule',params=params,json={'document':'{}'},headers=h).status_code==422
    journal_body={'document':'{}'}
    assert client.post(base+'router-journal-list',params=params,json=journal_body).status_code==401
    assert client.post(base+'router-journal-list',params=params,json=journal_body,headers=bearer).status_code==403
    journals=client.post(base+'router-journal-list',params=params,json=journal_body,headers=h)
    assert journals.status_code==200,journals.text
    assert journals.headers['cache-control']=='no-store'
    assert journals.json()['result']['journals']==[]
    assert client.post(base+'router-journal-detail',params=params,json={'document':'{"monitor_id":"absent"}'},headers=h).status_code==404
    assert client.post(base+'router-journal-forget',params=params,json={'document':'{"monitor_id":"absent","expected_head_sha256":"'+'f'*64+'","confirmed":false}'},headers=h).status_code==403
    assert client.post(base+'compare',params=params,json=body,headers=bearer).status_code==403
    result=client.post(base+'compare',params=params,json=body,headers=h)
    assert result.status_code==200,result.text
    assert result.headers['cache-control']=='no-store'
    assert result.json()['result']['case_count']==1 and result.json()['result_persisted'] is False
    assert 'PRIVATE_' not in result.text
    for bad in ({'document':'{"x":1,"x":2}'},{'document':'{"x":"PRIVATE_SENTINEL"}'},{'document':{}},{'document':'{}','extra':True}):
        rejected=client.post(base+'compare',params=params,json=bad,headers=h)
        assert rejected.status_code==422 and 'PRIVATE_SENTINEL' not in rejected.text
    design={'format':'openworkgraph.procurement-trial-design.v1','reference_report':report,
            'baseline_candidate_id':'current','currency':'USD','repeats':2,'schedule_seed':1,'confirmed':True,
            'gates':{'minimum_match_basis_points':0,'require_all_categories':False},
            'candidates':[{'candidate_id':cid,'provider':'provider','model':cid,'runtime':'cli','configuration_sha256':digest(cid)}
                          for cid in ('current','other')]}
    frozen=client.post(base+'freeze',params=params,json={'document':json.dumps(design)},headers=h)
    assert frozen.status_code==200,frozen.text
    plan=frozen.json()['result'];assert plan['scheduled_attempts']==4
    captures={'format':'openworkgraph.procurement-trial-captures.v1','plan_sha256':plan['plan_sha256'],'captures':[]}
    reviewed=client.post(base+'review',params=params,json={'document':json.dumps(captures),'plan':json.dumps(plan)},headers=h)
    assert reviewed.status_code==200,reviewed.text
    assert reviewed.json()['result']['candidates'][0]['status_counts']['not_recorded']==2
    assert reviewed.json()['result']['pareto']['candidate_ids']==[]
    economics={'plan':plan,'captures':captures,'request':{
        'format':'openworkgraph.procurement-operating-cost.v1',
        'plan_sha256':plan['plan_sha256'],'ledger_sha256':reviewed.json()['result']['ledger_sha256'],
        'currency':'USD','confirm_non_overlapping_cost_boundary':True,
        'rates':{k+'_hourly_microunits':100 for k in ('routine','review','rework','fallback','escalation')},
        'manual':[],'interventions':[],
        'scenario':{'annual_cases':100,'manual_annual_microunits':0,
                    'candidate_fixed_costs':[{'candidate_id':cid,'annual_microunits':0,'one_time_microunits':0}
                                             for cid in ('current','other')]}}}
    economic_body={'document':json.dumps(economics)}
    assert client.post(base+'operating-cost',params=params,json=economic_body,headers=bearer).status_code==403
    economic=client.post(base+'operating-cost',params=params,json=economic_body,headers=h)
    assert economic.status_code==200,economic.text
    assert economic.headers['cache-control']=='no-store'
    e=economic.json()
    assert not e['result_persisted'] and not e['execution_authorized']
    assert e['result']['manual_baseline']['attempts']==2
    assert e['result']['candidates'][0]['paired_trial_savings']=={'lower_microunits':None,'upper_microunits':None}
    wrong=deepcopy(economics);wrong['request']['ledger_sha256']='f'*64
    assert client.post(base+'operating-cost',params=params,json={'document':json.dumps(wrong)},headers=h).status_code==422
    actual=report['cases'][0]['category']
    cats=('normal','exception','missing_facts')
    portfolio={'category_volumes':[{'category':c,'annual_cases':100 if c==actual else 0} for c in cats],
               'portfolios':[{'portfolio_id':'manual-only','assignments':[{'category':c,'candidate_id':None} for c in cats],
                 'routing_annual_microunits':0,'routing_one_time_microunits':0,
                 'routing_cost_per_ai_case_microunits':0,'routing_human_ms_per_ai_case':0}],
               'confirm_conditional_category_assignment':True,'confirm_non_overlapping_routing_costs':True}
    economics['request']['scenario']['portfolio']=portfolio
    p=client.post(base+'operating-cost',params=params,json={'document':json.dumps(economics)},headers=h)
    assert p.status_code==200,p.text
    result=p.json()['result']['conditional_portfolios']
    assert not result['execution_authorized']
    assert result['portfolios'][0]['annual_declared_regression_count']['upper']['numerator']=='0'
    assert result['portfolios'][0]['recurring_savings']['lower_microunits']['numerator']=='0'
    assert p.headers['cache-control']=='no-store'
    for row in portfolio['category_volumes']:
        row['annual_cases']=0 if row['category']==actual else 50
    assert client.post(base+'operating-cost',params=params,json={'document':json.dumps(economics)},headers=h).status_code==422
    held=deepcopy(design)
    held['reference_report']['suite_sha256']='e'*64
    held['reference_report']['cases'][0]['case_sha256']='f'*64
    holdout=client.post(base+'freeze',params=params,json={'document':json.dumps(held)},headers=h).json()['result']
    def complete(p):
        rows=[]
        for cid in ('current','other'):
            for repeat in (1,2):
                r=deepcopy(p['basis']['reference_report'])
                r['results_sha256']=digest([cid,repeat,p['plan_sha256']])
                rows.append({'candidate_id':cid,'repeat':repeat,'configuration_sha256':digest(cid),'report':r,
                             'attempts':[{'case_id':'case_1','status':'completed','cost_microunits':100,'elapsed_ms':500}]})
        return {'format':'openworkgraph.procurement-trial-captures.v1','plan_sha256':p['plan_sha256'],'captures':rows}
    bundle={'discovery_plan':plan,'discovery_captures':complete(plan),'holdout_plan':holdout,
            'request':{'candidate_id':'other','nominator_alias':'a','confirmed':True,
                       'policy':{'minimum_discovery_cases':1,'minimum_holdout_cases':1,
                                 'minimum_candidate_match_basis_points':0,'require_all_categories':False}}}
    def selection(action,value,headers=h):
        return client.post(base+action,params=params,json={'document':json.dumps(value)},headers=headers)
    # Router rehearsal uses the exact same authenticated saved revision,
    # including a disjoint held-out trial and no assumed missing fallback.
    for row in portfolio['category_volumes']:
        row['annual_cases']=100 if row['category']==actual else 0
    portfolio['portfolios'][0]['portfolio_id']='selective'
    for row in portfolio['portfolios'][0]['assignments']:
        row['candidate_id']='current' if row['category']==actual else None
    router_request={'portfolio_id':'selective','router_configuration_sha256':digest('router'),
                    'policy':{'minimum_distinct_cases_per_positive_category':1},
                    'confirm_manual_fallback_on_known_router_failure':True,
                    'confirm_non_overlapping_router_measurements':True,'confirmed':True}
    router_bundle={'discovery':economics,'holdout_plan':holdout,'request':router_request}
    assert selection('router-freeze',router_bundle,bearer).status_code==403
    router_frozen=selection('router-freeze',router_bundle)
    assert router_frozen.status_code==200,router_frozen.text
    router_plan=router_frozen.json()['result']
    held_econ=deepcopy(economics);held_econ['plan']=holdout
    held_econ['captures']={'format':'openworkgraph.procurement-trial-captures.v1','plan_sha256':holdout['plan_sha256'],'captures':[]}
    held_review=client.post(base+'review',params=params,headers=h,json={'document':json.dumps(held_econ['captures']),'plan':json.dumps(holdout)})
    assert held_review.status_code==200,held_review.text
    held_econ['request'].update(plan_sha256=holdout['plan_sha256'],ledger_sha256=held_review.json()['result']['ledger_sha256'])
    router_captures={'format':'openworkgraph.procurement-router-captures.v1','router_plan_sha256':router_plan['router_plan_sha256'],
                     'router_configuration_sha256':router_request['router_configuration_sha256'],'predictions':[]}
    route_bundle={'discovery':economics,'holdout':held_econ,'router_plan':router_plan,'captures':router_captures}
    routed=selection('router-review',route_bundle)
    assert routed.status_code==200,routed.text
    assert routed.headers['cache-control']=='no-store'
    assert routed.json()['result']['checks']['router_capture_coverage']=='unknown'
    assert routed.json()['result']['scheduled_attempts']==2
    assert not routed.json()['execution_authorized']
    assert selection('router-review',route_bundle,bearer).status_code==403
    bad_route=deepcopy(route_bundle);bad_route['captures']['router_configuration_sha256']='f'*64
    assert selection('router-review',bad_route).status_code==422
    change_request={'incumbent_configuration_sha256':digest('router'),
                    'challenger_configuration_sha256':digest('router-new'),'portfolio_id':'selective',
                    'router_policy':router_request['policy'],'comparison_policy':{},
                    'nominator_alias':'operator-a','incremental_annual_switch_cost_microunits':0,
                    'incremental_one_time_switch_cost_microunits':0,
                    'confirm_manual_fallback_on_known_router_failure':True,
                    'confirm_non_overlapping_router_measurements':True,
                    'confirm_non_overlapping_incremental_switch_cost':True,'confirmed':True}
    change_freeze_bundle={'discovery':economics,'holdout_plan':holdout,'request':change_request}
    assert selection('router-change-freeze',change_freeze_bundle,bearer).status_code==403
    change_frozen=selection('router-change-freeze',change_freeze_bundle)
    assert change_frozen.status_code==200,change_frozen.text
    change_plan=change_frozen.json()['result']
    change_captures={k:{'format':'openworkgraph.procurement-router-captures.v1',
        'router_plan_sha256':change_plan[k+'_plan']['router_plan_sha256'],
        'router_configuration_sha256':change_plan[k+'_plan']['request']['router_configuration_sha256'],
        'predictions':[]} for k in ('incumbent','challenger')}
    change_bundle={'discovery':economics,'holdout':held_econ,'change_plan':change_plan,'captures':change_captures}
    change_assessed=selection('router-change-assess',change_bundle)
    assert change_assessed.status_code==200,change_assessed.text
    assert change_assessed.headers['cache-control']=='no-store'
    change_result=change_assessed.json()['result']
    assert not change_result['eligible_for_controlled_change_review']
    change_decision={**change_bundle,'request':{'disposition':'retain_incumbent','reviewer_alias':'reviewer-b',
        'expected_plan_sha256':change_plan['change_plan_sha256'],'expected_report_sha256':change_result['report_sha256'],'confirmed':True}}
    assert selection('router-change-decide',change_decision,bearer).status_code==403
    change_receipt=selection('router-change-decide',change_decision)
    assert change_receipt.status_code==200,change_receipt.text
    assert change_receipt.json()['result']['referred_configuration_sha256'] is None
    change_decision['request']['disposition']='refer_for_controlled_change_review'
    assert selection('router-change-decide',change_decision).status_code==422
    assert selection('nominate',bundle,bearer).status_code==403
    nominated=selection('nominate',bundle)
    assert nominated.status_code==200,nominated.text
    nomination=nominated.json()['result']
    assess_bundle={k:v for k,v in bundle.items() if k!='request'}
    assess_bundle.update(holdout_captures=complete(holdout),nomination=nomination)
    assessed=selection('assess',assess_bundle)
    assert assessed.status_code==200,assessed.text
    a=assessed.json()['result']
    assert a['eligible_for_rehearsal_review']
    decision={**assess_bundle,'request':{'decision':'select_for_substitution_rehearsal','reviewer_alias':'b',
              'expected_nomination_sha256':a['nomination_sha256'],'expected_assessment_sha256':a['assessment_sha256'],
              'confirmed':True}}
    decided=selection('decide',decision)
    assert decided.status_code==200,decided.text
    assert decided.json()['result']['rehearsal_candidate_id']=='other'
    assert not decided.json()['result']['execution_authorized']
    assert decided.headers['cache-control']=='no-store'
    from shared.agent_procurement_outcomes import freeze as freeze_outcomes
    from shared.agent_procurement_substitution import inspect as inspect_artifacts
    from shared.process_agent_project import build_runtime_project
    from shared.process_agent_release import MANIFEST_NAME
    from io import BytesIO
    from zipfile import ZipFile
    import hashlib
    receipt=decided.json()['result']
    artifact_root=Path(__import__('sys').argv[1])/'artifact';artifact_root.mkdir()
    with ZipFile(BytesIO(build_runtime_project(packet))) as z:z.extractall(artifact_root)
    pin=hashlib.sha256((artifact_root/MANIFEST_NAME).read_bytes()).hexdigest()
    pf=inspect_artifacts(packet,decision,receipt,artifact_root,artifact_root,
                         {'expected_selection_receipt_sha256':receipt['receipt_sha256'],'confirmed':True,
                          'baseline':{'configuration_sha256':digest('current'),'manifest_sha256':pin},
                          'candidate':{'configuration_sha256':digest('other'),'manifest_sha256':pin}})
    snapshot={'records':[{'record_id':'target','fields':{'state':'PRIVATE_STATE'}}]}
    outcome_plan=freeze_outcomes(packet,decision,pf,{'format':'openworkgraph.procurement-outcome-design.v1',
        'expected_selection_receipt_sha256':receipt['receipt_sha256'],'expected_preflight_sha256':pf['preflight_sha256'],
        'owner_alias':'a','sandbox_configuration_sha256':digest('sandbox'),'confirmed':True,
        'policy':{'minimum_distinct_cases':1,'require_all_categories':False},
        'cases':[{'case_id':'case_1','category':'normal','starting_state':snapshot,'expected_final_state':snapshot,
                  'permitted_actions':[],'reviewed':True}]})
    observations={'format':'openworkgraph.procurement-outcome-observations.v1','plan_sha256':outcome_plan['plan_sha256'],
                  'observations':[{**slot,'sandbox_configuration_sha256':digest('sandbox'),'observer_alias':'observer',
                      'isolated_run_id':slot['arm'],'started_at':'2026-01-01T00:00:00Z','ended_at':'2026-01-01T00:00:01Z',
                      'status':'completed','initial_snapshot':snapshot,'final_snapshot':snapshot,'observed_actions':[],
                      'action_capture_complete':True} for slot in outcome_plan['slots']]}
    outcome_bundle={'decision_bundle':decision,'preflight':pf,'outcome_plan':outcome_plan,'observations':observations}
    assert selection('outcome-review',outcome_bundle,bearer).status_code==403
    outcome=selection('outcome-review',outcome_bundle)
    assert outcome.status_code==200,outcome.text
    assert 'PRIVATE_STATE' not in outcome.text
    review=outcome.json()['result']
    assert review['eligible_to_request_separate_rollout_review']
    rollout={**outcome_bundle,'request':{'decision':'request_separate_rollout_review','reviewer_alias':'b',
        'expected_outcome_review_sha256':review['outcome_review_sha256'],'expected_plan_sha256':review['plan_sha256'],
        'rollback_plan_sha256':digest('rollback'),'confirmed':True}}
    result=selection('rollout-review',rollout)
    assert result.status_code==200,result.text
    assert result.json()['result']['separate_rollout_review_requested']
    assert not result.json()['result']['production_rollout_authorized']
    assert 'PRIVATE_STATE' not in result.text
    updated=client.post('/v1/process-catalog',headers=h,json={'workflow_id':saved['workflow_id'],'title':'Updated','expected_revision':1,'user_confirmed':True,'process_model':model()})
    assert updated.status_code==200,updated.text
    assert selection('router-change-freeze',change_freeze_bundle).status_code==409
    assert selection('router-change-decide',change_decision).status_code==409
    assert selection('router-change-assess',change_bundle).json()['references_latest_revision_at_inspection'] is False
    assert selection('router-freeze',router_bundle).status_code==409
    assert selection('router-review',route_bundle).json()['references_latest_revision_at_inspection'] is False
    assert selection('nominate',bundle).status_code==409
    assert selection('decide',decision).status_code==409
    assert selection('rollout-review',rollout).status_code==409
    assert selection('outcome-review',outcome_bundle).json()['references_latest_revision_at_inspection'] is False
    assert selection('assess',assess_bundle).json()['references_latest_revision_at_inspection'] is False
    historical=client.post(base+'compare',params=params,json=body,headers=h).json()
    assert historical['references_latest_revision_at_inspection'] is False
    assert client.post(base+'compare',params={**params,'revision':2},json=body,headers=h).status_code==422
    assert client.post(base+'compare',params={**params,'workflow_id':'other'},json=body,headers=h).status_code==404
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM workflow_knowledge').fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    run=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+code,str(tmp_path)],
                       cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stdout+run.stderr
