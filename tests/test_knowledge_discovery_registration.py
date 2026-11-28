"""Exercise the existing tool registration and authorization/finish boundaries."""
from types import SimpleNamespace
import pytest

from mcp_server.workflow_evidence_tools import register_workflow_evidence_tools
from test_knowledge_discovery import rec, response


class Server:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register

    def resource(self, *args, **kwargs):
        return lambda fn: fn

    def prompt(self, **kwargs):
        return lambda fn: fn


def test_discovery_uses_existing_authorized_read_and_legacy_path():
    server = Server()
    calls = []
    data = {**response(rec(1,'Invoices'), rec(2,'Renewals'), more=True), 'next_after':'process_2'}
    def authorize(name):
        calls.append(('authorize',name))
    def read(path, params):
        calls.append(('get',path,params))
        return data
    def finish(name, value):
        calls.append(('finish',name))
        return value
    runtime = SimpleNamespace(authorize_tool=authorize, secure_get=read,
        core=SimpleNamespace(_finish_reviewed_knowledge=finish))
    register_workflow_evidence_tools(server,runtime)
    tool = server.tools['get_workflow_knowledge']
    result = tool(query='invoices',limit=5)
    assert [r['workflow_id'] for r in result['candidates']] == ['process_1']
    assert calls == [('authorize','get_workflow_knowledge'),
        ('get','/v1/workflow-knowledge',{'workflow_id':'','limit':100,'after':''}),
        ('finish','get_workflow_knowledge')]
    assert result['next_after']=='process_2'
    calls.clear()
    data = {**response(rec(3,'Invoices')), 'next_after':None}
    result=tool(query='invoices',limit=5,after='process_2',scan_limit=20)
    assert result['candidates'][0]['workflow_id']=='process_3' and not result['has_more']
    assert calls == [('authorize','get_workflow_knowledge'),('get','/v1/workflow-knowledge',{'workflow_id':'','limit':20,'after':'process_2'}),('finish','get_workflow_knowledge')]
    calls.clear()
    assert tool(workflow_id='process_1',limit=3) is data
    assert calls == [('authorize','get_workflow_knowledge'),
        ('get','/v1/workflow-knowledge',{'workflow_id':'process_1','limit':3}),
        ('finish','get_workflow_knowledge')]
    calls.clear()
    with pytest.raises(ValueError):
        tool(workflow_id='process_1',query='invoices')
    assert calls == [('authorize','get_workflow_knowledge')]
    def deny(name):
        raise PermissionError('AI access disabled')
    runtime.authorize_tool = deny
    calls.clear()
    with pytest.raises(PermissionError):
        tool(summary_only=True)
    assert calls == []
