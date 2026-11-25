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
    updated=client.post('/v1/process-catalog',headers=h,json={'workflow_id':saved['workflow_id'],'title':'Updated','expected_revision':1,'user_confirmed':True,'process_model':model()})
    assert updated.status_code==200,updated.text
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
