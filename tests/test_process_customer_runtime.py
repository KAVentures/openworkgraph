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
def test_customer_project_executes_exact_mcp_calls_and_checks_independent_source_state(tmp_path):
    sdk=os.environ['OWG_AGENT_SDK_PYTHON']
    model=ProcessModel(goal='Resolve a selected synthetic case',baseline_source='user_declared',
        baseline_path=['Resolve','Read case','Done'],activities=[
            {'id':'resolve','label':'Resolve','implementation':'api'},
            {'id':'readback','label':'Read case','implementation':'api'},
            {'id':'done','label':'Done','kind':'end'}],
        transitions=[{'source':'resolve','target':'readback'},{'source':'readback','target':'done'}],
        entry_activity_id='resolve',terminal_activity_ids=['done'],outcome_expectations=[
            {'terminal_activity_id':'done','operation':'jira_issue_resolved','user_confirmed':True}])
    packet={'format':'openworkgraph.versioned-process-export.v1','workflow_id':'synthetic','revision':1,
        'content_sha256':'a'*64,'process_model':model.model_dump(),'execution_authorization':False}
    project=tmp_path/'project';project.mkdir()
    with ZipFile(BytesIO(build_runtime_project(packet))) as archive:archive.extractall(project)
    app_file=tmp_path/'synthetic_mcp.py'
    app_file.write_text(r'''
from datetime import datetime,timezone
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
state={'version':3,'resolved':False,'tool_calls':[],'source_reads':0}
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
    expected='Bearer SYNTHETIC_TOOL_SECRET' if request.url.path.startswith('/mcp') else 'Bearer SYNTHETIC_SOURCE_SECRET'
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
async def stats(request):return JSONResponse(state)
from starlette.routing import Route
app.router.routes.extend([Route('/v1/record-graph/operation-receipt',source,methods=['GET']),Route('/test-state',stats,methods=['GET'])])
''',encoding='utf-8')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    env=os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),
        WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'),PYTHONPATH=str(ROOT),OWG_TEST_SERVER_URL=base,
        OWG_EXECUTION_MCP_TOKEN='SYNTHETIC_TOOL_SECRET',OWG_EXECUTION_SOURCE_READ_TOKEN='SYNTHETIC_SOURCE_SECRET')
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
bindings.update(tool_server_url=os.environ['OWG_TEST_SERVER_URL']+'/mcp',source_api_url=os.environ['OWG_TEST_SERVER_URL'])
for name,data in [('plan.json',plan),('case.json',case),('bindings.json',bindings)]:
    with open(name,'w') as f:json.dump(data,f)
''')
            assert setup.returncode==0,setup.stderr
            args=['execute.py','--plan','plan.json','--case','case.json','--bindings','bindings.json']
            offline=run(*args,'--write-review-template','intent.json')
            assert offline.returncode==0,offline.stderr
            review=json.loads(offline.stdout)
            assert review['network_contacted'] is False and review['requirements']==[]
            assert stats()['tool_calls']==[] and stats()['source_reads']==0
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
            repeated=run(*args,'--intent','intent.json','--execute-approved')
            assert repeated.returncode!=0
            assert stats()==observed
            for private in ('SYNTHETIC_TOOL_SECRET','SYNTHETIC_SOURCE_SECRET','SYNTHETIC_PRIVATE_RECORD'):
                assert private not in execute.stdout+execute.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
