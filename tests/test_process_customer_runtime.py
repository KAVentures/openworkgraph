"""Real SDK MCP transport against owned synthetic tools and source-state HTTP.

Run with OWG_AGENT_SDK_PYTHON pointing to an isolated environment containing
openai-agents 0.24. No model or live business system is contacted.
"""
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pytest

from server.process_agent_project import build_runtime_project
from shared.process_model import ProcessModel

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not os.getenv('OWG_AGENT_SDK_PYTHON'),reason='Set an isolated Agents SDK interpreter for the real MCP implementation check')
@pytest.mark.parametrize('shared_catalog,source_invariant',[(False,False),(True,False),(True,True),(True,'decision')])
@pytest.mark.parametrize('parallel_schedule',[False,True,'nested'])
def test_customer_project_executes_exact_mcp_calls_and_checks_independent_source_state(tmp_path,monkeypatch,shared_catalog,source_invariant,parallel_schedule):
    sdk=os.environ['OWG_AGENT_SDK_PYTHON']
    model=ProcessModel(goal='Resolve a selected synthetic case',baseline_source='user_declared',
        baseline_path=['Resolve','Read case','Done'],activities=[
            {'id':'resolve','label':'Resolve','implementation':'api'},
            {'id':'readback','label':'Read case','implementation':'api'},
            {'id':'done','label':'Done','kind':'end'}],
        transitions=[{'source':'resolve','target':'readback'},{'source':'readback','target':'done'}],
        entry_activity_id='resolve',terminal_activity_ids=['done'],outcome_expectations=[
            {'terminal_activity_id':'done','operation':'jira_issue_resolved','user_confirmed':True}])
    if parallel_schedule:
        raw=model.model_dump()
        raw['activities']=[{'id':'fork','label':'Both activities','kind':'parallel_split'},
            *raw['activities'][:-1],{'id':'join','label':'Both completed','kind':'parallel_join'},raw['activities'][-1]]
        raw['transitions']=[{'source':a,'target':b} for a,b in
            [('fork','resolve'),('fork','readback'),('resolve','join'),('readback','join'),('join','done')]]
        raw['entry_activity_id']='fork';model=ProcessModel.model_validate(raw)
        if parallel_schedule=='nested':
            raw=model.model_dump()
            raw['activities'] += [{'id':'innerFork','label':'Nested checks','kind':'parallel_split'},
                                  {'id':'innerJoin','label':'Nested checks complete','kind':'parallel_join'}]
            raw['transitions']=[e for e in raw['transitions'] if (e['source'],e['target']) not in
                                {('fork','resolve'),('resolve','join')}]
            raw['transitions'] += [{'source':a,'target':b} for a,b in
                [('fork','innerFork'),('innerFork','resolve'),('innerFork','innerJoin'),
                 ('resolve','innerJoin'),('innerJoin','join')]]
            model=ProcessModel.model_validate(raw)
    if source_invariant:
        raw=model.model_dump()
        raw['business_rules']=[{'id':'approved_source','text':'Reviewed current source invariant','user_confirmed':True,
            'predicate':{'match':'all','conditions':[{'path':'/source/state','operator':'eq','expected':'merged'}]}}]
        if parallel_schedule:
            raw['activities'] += [{'id':'choose','label':'Choose completion','kind':'decision'},
                                  {'id':'reject','label':'Needs review','kind':'end'}]
            raw['transitions']=[e for e in raw['transitions'] if e['source']!='join']+[
                {'source':'join','target':'choose'}, {'source':'choose','target':'done','rule_id':'approved_source'},
                {'source':'choose','target':'reject','rule_id':'needs_review'}]
            raw['business_rules'].append({'id':'needs_review','text':'Alternative needs owner clarification','user_confirmed':False})
            raw['terminal_activity_ids'].append('reject')
        else:raw['transitions'][0]['rule_id']='approved_source'
        model=ProcessModel.model_validate(raw)
    if source_invariant=='decision':
        raw=model.model_dump()
        if not parallel_schedule:
            raw['activities'] += [{'id':'choose','label':'Current decision','kind':'decision'},
                                  {'id':'reject','label':'Hold','kind':'end'}]
            raw['entry_activity_id']='choose';raw['terminal_activity_ids'].append('reject')
            raw['transitions'][0].pop('rule_id')
            raw['transitions'] += [{'source':'choose','target':'resolve','rule_id':'approved_source'},
                                  {'source':'choose','target':'reject','rule_id':'needs_review'}]
            raw['business_rules'].append({'id':'needs_review','text':'Other current state','user_confirmed':True})
        other=next(rule for rule in raw['business_rules'] if rule['id']=='needs_review')
        other.update(user_confirmed=True,predicate={'conditions':[{'path':'/source/state','operator':'ne','expected':'merged'}]})
        raw['decision_tables']=[{'id':'current_decision','decision_activity_id':'choose',
            'effective_from':'2000-01-01','effective_until':'2100-01-01',
            'source_basis':{'label':'Synthetic policy declaration','document_sha256':'d'*64},
            'rows':[{'id':'row_'+edge['rule_id'],'rule_id':edge['rule_id'],'target_activity_id':edge['target']}
                    for edge in raw['transitions'] if edge['source']=='choose'], 'user_confirmed':True}]
        model=ProcessModel.model_validate(raw)
    packet={'format':'openworkgraph.versioned-process-export.v1','workflow_id':'synthetic','revision':1,
        'content_sha256':'a'*64,'process_model':model.model_dump(),'execution_authorization':False}
    if shared_catalog:packet['organization_catalog_reference']={'organization_id':'synthetic-org'}
    project=tmp_path/'project';project.mkdir()
    with ZipFile(BytesIO(build_runtime_project(packet))) as archive:archive.extractall(project)
    app_file=tmp_path/'synthetic_mcp.py'
    app_file.write_text(r'''
from datetime import datetime,timezone
import os
try:
    from mcp.server.mcpserver import MCPServer
    mcp=MCPServer('Synthetic customer implementation')
    v2=True
except ImportError:
    from mcp.server.fastmcp import FastMCP
    mcp=FastMCP('Synthetic customer implementation',stateless_http=True,json_response=True)
    v2=False
from starlette.responses import JSONResponse
from server.open_work_outcomes import source_operation_receipt
from threading import Lock
guard=Lock()
completed_requests={}
state={'version':3,'resolved':False,'tool_calls':[],'source_reads':0,'catalog_reads':0,'catalog_current':True,'source_rule_reads':0}
@mcp.tool()
def read_case(issue:str)->dict:
    with guard:
        state['tool_calls'].append('read_case')
        return {'success':issue=='SYNTHETIC_PRIVATE_RECORD','version':state['version'] if issue=='SYNTHETIC_PRIVATE_RECORD' else None}
@mcp.tool()
def resolve_case(issue:str,expected_version:int,request_id:str)->dict:
    with guard:
        state['tool_calls'].append('resolve_case')
        if request_id in completed_requests:
            return {'success':completed_requests[request_id]==(issue,expected_version)}
        if issue!='SYNTHETIC_PRIVATE_RECORD' or expected_version!=state['version'] or not request_id:
            return {'success':False}
        state['resolved']=True;state['version']+=1
        completed_requests[request_id]=(issue,expected_version)
        return {'success':True}
app=mcp.streamable_http_app(stateless_http=True,json_response=True) if v2 else mcp.streamable_http_app()
async def auth(request,call_next):
    expected=('Bearer SYNTHETIC_TOOL_SECRET' if request.url.path.startswith('/mcp') else
        'Bearer SYNTHETIC_CATALOG_SECRET' if request.url.path.startswith('/v1/human/') else 'Bearer SYNTHETIC_SOURCE_SECRET')
    if request.headers.get('authorization')!=expected:return JSONResponse({'error':'synthetic credential required'},status_code=401)
    return await call_next(request)
from starlette.middleware.base import BaseHTTPMiddleware
app.add_middleware(BaseHTTPMiddleware,dispatch=auth)
async def source(request):
    state['source_reads']+=1
    ref=request.query_params.get('resource_ref','')
    op=request.query_params.get('expected_operation','')
    # Real OWG receipt logic reads this separately held synthetic source state.
    # It never trusts the tool's success reply or the client's own outcome claim.
    def graph(*args,**kwargs):
        return {'record_observed':ref=='owg:r:'+'b'*32,
            'record_identity_complete_within_retained_window':True,
            'record_type':{'provider':'jira','kind':'issue'},'source_evidence':{
                'availability':'source_reported','source_state_reported':True,
                'verified_at':datetime.now(timezone.utc).isoformat(),
                'source_state':'done' if state['resolved'] else 'open',
                'verified_operational_outcome':{'verified':state['resolved'],'kind':'jira_issue_resolved'}}}
    return JSONResponse(source_operation_receipt(ref,op,record_reader=graph))
async def rule_source(request):
    from server.process_source_rule_checks import SourceRuleCheck,current_source_state_receipt
    state['source_rule_reads']+=1
    assert request.query_params.get('confirm_source_read')=='true'
    selection=SourceRuleCheck(resource_ref=request.query_params['resource_ref'],source_type=request.query_params['source_type'])
    def graph(ref,**kwargs):
        assert ref=='owg:r:'+'c'*32 and kwargs['required_source_type']==('github','pull_request','github.com')
        return {'resource_ref':ref,'record_observed':True,'record_type':{'provider':'github','kind':'pull_request'},
            'record_identity_complete_within_retained_window':True,'source_event_limit_reached':False,
            'source_rows_scoped_to_exact_record':True,'source_evidence':{
                'provider':'github','availability':'source_reported','source_state_reported':True,'source_state':'merged',
                'verification_method':'explicit_opt_in_authenticated_local_github_cli_read',
                'verified_at':datetime.now(timezone.utc).isoformat()}}
    return JSONResponse(current_source_state_receipt(selection,record_reader=graph))
async def catalog(request):
    state['catalog_reads']+=1
    return JSONResponse({'format':'openworkgraph.process-implementation-state.v1','organization_id':'synthetic-org',
        'process_reference':{'workflow_id':'synthetic','revision':1,'content_sha256':'a'*64},
        'process_model_sha256':os.environ['OWG_TEST_MODEL_DIGEST'],'latest_revision':1,'archived':False,
        'review_status':'reviewed_design','current_independent_design_review':state['catalog_current'],
        'execution_authorization':False})
async def revoke(request):
    state['catalog_current']=False
    return JSONResponse({'changed':True})
async def stats(request):return JSONResponse(state)
from starlette.routing import Route
app.router.routes.extend([Route('/v1/record-graph/state-receipt',rule_source,methods=['GET']),Route('/v1/record-graph/operation-receipt',source,methods=['GET']),Route('/test-state',stats,methods=['GET']),
    Route('/v1/human/processes/synthetic/implementation-status',catalog,methods=['GET']),
    Route('/test-revoke',revoke,methods=['POST'])])
''',encoding='utf-8')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    env=os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),
        WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'),PYTHONPATH=str(ROOT),OWG_TEST_SERVER_URL=base,
        OWG_EXECUTION_MCP_TOKEN='SYNTHETIC_TOOL_SECRET',OWG_EXECUTION_SOURCE_READ_TOKEN='SYNTHETIC_SOURCE_SECRET',
        OWG_EXECUTION_CATALOG_READ_TOKEN='SYNTHETIC_CATALOG_SECRET',
        OWG_TEST_MODEL_DIGEST=json.loads((project/'workflow.spec.json').read_text())['process_model_sha256'],
        OWG_TEST_SOURCE_INVARIANT='1' if source_invariant else '0',
        OWG_TEST_DECISION='1' if source_invariant=='decision' else '0',
        OWG_TEST_PARALLEL_SCHEDULE='nested' if parallel_schedule=='nested' else '1' if parallel_schedule else '0')
    env['NO_PROXY']=','.join(filter(None,[env.get('NO_PROXY',''),'127.0.0.1','localhost','::1']))
    def stats():
        with urlopen(Request(base+'/test-state',headers={'Authorization':'Bearer SYNTHETIC_SOURCE_SECRET'}),timeout=2) as response:
            return json.load(response)
    log=tmp_path/'server.log'
    with log.open('w') as output:
        server=subprocess.Popen([sys.executable,'-m','uvicorn','synthetic_mcp:app','--app-dir',str(tmp_path),
            '--host','127.0.0.1','--port',str(port),'--log-level','warning'],env=env,stdout=output,stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if server.poll() is not None:pytest.fail('Synthetic MCP server exited: '+log.read_text()[-2000:])
                try:
                    stats();break
                except Exception:time.sleep(.1)
            else:pytest.fail('Synthetic MCP server failed to start')
            def run(*args):
                return subprocess.run([sdk,*args],cwd=project,env=env,capture_output=True,text=True,timeout=60)
            setup=run('-c',r'''
import json,os
from test_execution import fixture
_,plan,case,bindings,_=fixture()
if os.environ['OWG_TEST_PARALLEL_SCHEDULE']!='0':
    prefix=(['fork','innerFork','resolve','innerJoin','readback','join']
        if os.environ['OWG_TEST_PARALLEL_SCHEDULE']=='nested' else ['fork','resolve','readback','join'])
    plan['activity_ids']=prefix+(['choose'] if os.environ['OWG_TEST_SOURCE_INVARIANT']=='1' else [])+['done']
if os.environ['OWG_TEST_SOURCE_INVARIANT']=='1':
    case['approved_record']='owg:r:'+'c'*32
    bindings['source_rule_checks']=[{'rule_id':'approved_source','source_type':'github_pull_request',
        'resource_ref_pointer':'/approved_record','mode':'invariant_before_each_action',
        'record_semantics_reviewed':True,'non_atomic_reads_reviewed':True}]
if os.environ['OWG_TEST_DECISION']=='1':
    bindings.pop('source_rule_checks',None)
    bindings['decision_table_checks']=[{'table_id':'current_decision','source_type':'github_pull_request',
        'resource_ref_pointer':'/approved_record','mode':'unique_route_before_each_action',
        'record_semantics_reviewed':True,'non_atomic_reads_reviewed':True,'utc_effective_dates_reviewed':True}]
    if os.environ['OWG_TEST_PARALLEL_SCHEDULE']=='0':plan['activity_ids'].insert(0,'choose')
bindings.update(tool_server_url=os.environ['OWG_TEST_SERVER_URL']+'/mcp',source_api_url=os.environ['OWG_TEST_SERVER_URL'],
    process_catalog_url=os.environ['OWG_TEST_SERVER_URL'])
for name,data in [('plan.json',plan),('case.json',case),('bindings.json',bindings)]:
    with open(name,'w') as f:json.dump(data,f)
''')
            assert setup.returncode==0,setup.stderr
            inspection=run('configure.py','--inspect-tools','--tool-server-url',base+'/mcp','--output','tool-catalog.json')
            assert inspection.returncode==0,inspection.stderr
            catalog=json.loads((project/'tool-catalog.json').read_text())
            assert {tool['name'] for tool in catalog['tools']}=={'read_case','resolve_case'}
            assert stats()['tool_calls']==[] and stats()['source_reads']==0 and stats()['catalog_reads']==0
            configuration_tests=run('-m','unittest','-v','test_configuration','test_evaluation','test_agent_sdk','test_source_planning','test_source_evaluation')
            assert configuration_tests.returncode==0,configuration_tests.stdout+configuration_tests.stderr
            pinned=run('-c',r'''
import json
catalog=json.load(open('tool-catalog.json'))
bindings=json.load(open('bindings.json'))
schemas={tool['name']:tool['input_schema_sha256'] for tool in catalog['tools']}
for row in bindings['bindings']:row['tool_schema_sha256']=schemas[row['tool_name']]
json.dump(bindings,open('bindings.json','w'))
''')
            assert pinned.returncode==0,pinned.stderr
            if source_invariant:
                planning=run('-c',r'''
import asyncio,json
from plan_sources import propose_with_sources
from runtime import load_spec
from test_agent_sdk import OfflineModel
case=json.load(open('case.json'));case['text']='SYNTHETIC_UNTRUSTED_INSTRUCTION'
plan=json.load(open('plan.json'));plan['missing_business_inputs']=[]
report=asyncio.run(propose_with_sources(case,json.load(open('bindings.json')),model=OfflineModel(plan),spec=load_spec(),
    source_reads_confirmed=True,share_rule_checks_with_model_confirmed=True))
print(json.dumps(report))
''')
                assert planning.returncode==0,planning.stdout+planning.stderr
                proposal=json.loads(planning.stdout)
                assert proposal['plan']['status']=='proposal' and not proposal['execution_permitted']
                assert proposal['source_planning']['source_receipt_request_count']==1
                if source_invariant=='decision':
                    assert proposal['source_planning']['rule_checks']==[]
                    assert proposal['source_planning']['decision_table_checks'][0]['route_matches']
                else:assert proposal['source_planning']['rule_checks']==[{'rule_id':'approved_source','status':'matched'}]
                assert 'SYNTHETIC_SOURCE_SECRET' not in planning.stdout and 'merged' not in planning.stdout
                evaluation=run('-c',r'''
import asyncio,json
from evaluate_sources import evaluate_current_sources
from execute import digest,reference
from runtime import load_spec
from test_agent_sdk import OfflineModel
spec=load_spec();case=json.load(open('case.json'));case['text']='SYNTHETIC_UNTRUSTED_INSTRUCTION'
plan=json.load(open('plan.json'));plan['missing_business_inputs']=[]
suite={'format':'openworkgraph.reviewed-shadow-cases.v1','process_reference':reference(spec),
    'spec_sha256':digest(spec),'reviewed_by_customer':True,'case_inputs_authorized':True,
    'cases':[{'id':'case_1','category':'normal','input':case,'expected':{'status':'proposal',
        'accepted_routes':[plan['activity_ids']],'review_confirmed':True}}]}
report,summary=asyncio.run(evaluate_current_sources(spec,suite,json.load(open('bindings.json')),
    model=OfflineModel(plan),source_reads_confirmed=True,share_rule_checks_with_model_confirmed=True))
print(json.dumps(report))
''')
                assert evaluation.returncode==0,evaluation.stdout+evaluation.stderr
                from shared.process_shadow_review import ShadowEvaluation,inspect_shadow_report
                evaluated=ShadowEvaluation.model_validate_json(evaluation.stdout)
                assert evaluated.matching_case_count==1 and not evaluated.review_required
                inspected=inspect_shadow_report(packet,evaluated,latest_revision=1)
                assert not inspected['execution_authorized']
                before_execution=stats()
                assert before_execution['source_rule_reads']==2 and not before_execution['tool_calls']
                assert before_execution['source_reads']==0 and before_execution['catalog_reads']==4
            args=['execute.py','--plan','plan.json','--case','case.json','--bindings','bindings.json']
            offline=run(*args,'--write-review-template','intent.json')
            assert offline.returncode==0,offline.stderr
            review=json.loads(offline.stdout)
            assert review['network_contacted'] is False and review['requirements']==[]
            assert stats()['tool_calls']==[] and stats()['source_reads']==0 and stats()['catalog_reads']==(4 if source_invariant else 0)
            intent=json.loads((project/'intent.json').read_text())
            assert intent['customer_authorization_confirmed'] is False
            assert intent['source_reads_confirmed'] is False
            # The test operator approves ONLY these synthetic records/tools.
            from datetime import datetime,timedelta,timezone
            intent.update(customer_authorization_confirmed=True,source_reads_confirmed=True,
                expires_at=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat())
            (project/'intent.json').write_text(json.dumps(intent))
            execute=run(*args,'--intent','intent.json','--execute-approved')
            assert execute.returncode==0,execute.stdout+execute.stderr
            report=json.loads(execute.stdout)
            assert report['all_tool_results_matched'] is True,report
            assert report['all_declared_operations_verified'] is True,report
            assert report['business_objective_verified'] is False
            assert report['agent_causality_verified'] is False
            assert report['model_invoked'] is False
            observed=stats()
            assert observed['tool_calls']==['read_case','resolve_case','read_case']
            assert observed['resolved'] is True and observed['version']==4
            assert observed['source_reads']==2
            assert observed['source_rule_reads']==(7 if source_invariant=='decision' else 6 if source_invariant else 0)
            assert report['source_rules_matched_at_completion'] is True
            exported=run('monitor.py','--journal',f"execution-journal/{report['execution_id']}.jsonl",
                         '--deployment-name','Synthetic SDK implementation','--output','monitoring.json')
            assert exported.returncode==0,exported.stderr
            monitoring=json.loads((project/'monitoring.json').read_text())
            from server.automation_monitoring import parse_report
            parsed=parse_report(monitoring)
            assert parsed['runtime']=='openai-agents'
            assert parsed['runs'][0]['status']=='succeeded'
            assert parsed['runs'][0]['verified'] is None
            assert [step[0] for step in parsed['runs'][0]['steps']]==['resolve','readback']
            from server import automation_monitoring as monitoring_store
            monkeypatch.setattr(monitoring_store,'DATA_DIR',tmp_path/'monitoring')
            deployment=monitoring_store.import_report(monitoring)['deployment']['deployment_id']
            monitoring_store.attach_design(deployment,json.loads((project/'workflow.spec.json').read_text()))
            measured=monitoring_store.monitor(deployment)
            assert measured['design_conformance']['runs_compared']==1
            assert measured['design_conformance']['runs_exact_structural_match']==1
            assert measured['design_conformance']['current_catalog_review_verified'] is False
            assert measured['overall']['outcome_reported_verified'] is None
            monitoring_store.delete_deployment(deployment)
            assert monitoring_store.list_deployments()['deployments']==[]
            assert stats()==observed  # Offline projection cannot re-read or execute anything.
            overwrite=run('monitor.py','--journal',f"execution-journal/{report['execution_id']}.jsonl",
                          '--deployment-name','Synthetic SDK implementation','--output','monitoring.json')
            assert overwrite.returncode!=0
            assert json.loads((project/'monitoring.json').read_text())==monitoring
            repeated=run(*args,'--intent','intent.json','--execute-approved')
            assert repeated.returncode!=0
            assert stats()==observed
            assert observed['catalog_reads']==((9 if source_invariant else 5) if shared_catalog else 0)
            if shared_catalog:
                with urlopen(Request(base+'/test-revoke',method='POST',headers={'Authorization':'Bearer SYNTHETIC_SOURCE_SECRET'}),timeout=2):pass
                # A newly authorized synthetic intent must still check current design.
                intent['expires_at']=(datetime.now(timezone.utc)+timedelta(minutes=11)).isoformat()
                (project/'intent.json').write_text(json.dumps(intent))
                revoked=run(*args,'--intent','intent.json','--execute-approved')
                assert revoked.returncode==0,revoked.stderr
                rejected=json.loads(revoked.stdout)
                assert rejected['stopped_before_tools'] and not rejected['catalog_current_at_last_check']
                after=stats()
                assert after['tool_calls']==observed['tool_calls'] and after['source_reads']==2
                assert after['catalog_reads']==(10 if source_invariant else 6)
                assert after['source_rule_reads']==observed['source_rule_reads']
            for private in ('SYNTHETIC_TOOL_SECRET','SYNTHETIC_SOURCE_SECRET','SYNTHETIC_CATALOG_SECRET','SYNTHETIC_PRIVATE_RECORD'):
                assert private not in execute.stdout+execute.stderr
                assert private not in json.dumps(monitoring)+exported.stdout+exported.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
