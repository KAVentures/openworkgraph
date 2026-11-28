import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import ai_implementation_knowledge_routes as routes
from server import ai_implementation_batch_routes as batch
from shared import ai_implementation_review as contract
from shared import ai_implementation_knowledge_context as ctx
from test_ai_implementation_knowledge_context import record
from test_ai_implementation_review import source


def test_recompute_original_packet_and_saved_revision(monkeypatch):
    s=source(True); r=record(); p=contract.prepare([s])
    monkeypatch.setattr(routes,'latest',lambda _:r)
    monkeypatch.setattr(batch,'dashboard_session_valid',lambda v:v=='owner')
    app=FastAPI();app.include_router(routes.router);c=TestClient(app)
    url='/v1/ai-opportunity-handoff/knowledge-context'
    req={'sources':[json.dumps(s)],'packet_sha256':p['packet_sha256'],'max_bytes':contract.MAX_BYTES,'references':[ctx.reference(r)]}
    h={'Authorization':'OWG-Session owner'}
    assert c.post(url,json=req).status_code==403
    result=c.post(url,json=req,headers=h)
    assert result.status_code==200,result.text
    data=result.json(); context=json.loads(data['context_json'])
    assert context['reviewed_knowledge']==[r] and context['packet_sha256']==p['packet_sha256']
    assert context['context_sha256']==data['context_sha256'] and not data['execution_authorized']
    assert c.post(url,json={**req,'packet_sha256':'0'*64},headers=h).status_code==422
    r['revision']+=1
    assert c.post(url,json=req,headers=h).status_code==422


def test_persisted_plain_workflow_owner_boundary_and_combined_static_route(tmp_path, monkeypatch):
    from gateway.db import GatewayDB
    from shared.workflow_knowledge import KnowledgeStore, KnowledgeWrite
    from shared.process_catalog import ProcessCatalog
    from server import local_process_catalog as local
    store=KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "knowledge.db"}'))
    monkeypatch.setattr(local,'knowledge',store)
    monkeypatch.setattr(local,'catalog',ProcessCatalog(store,'local'))
    def saved(owner,key):
        return store.save(owner,KnowledgeWrite(workflow_id=key,title='Review invoice',
            procedure='Inspect invoice and confirm approval',source_client='test',user_confirmed=True))
    row=saved('local','proc_001'); hidden=saved('other','proc_002')
    monkeypatch.setattr(batch,'dashboard_session_valid',lambda v:v=='owner')
    app=FastAPI();app.include_router(routes.router);c=TestClient(app)
    h={'Authorization':'OWG-Session owner'}
    listing=c.post('/v1/ai-opportunity-handoff/knowledge-list',json={'after':''},headers=h)
    assert listing.status_code==200,listing.text
    assert listing.json()['processes']==[{**ctx.reference(row),'title':row['title'],'has_process_model':False}]
    s=source(True);p=contract.prepare([s])
    req={'sources':[json.dumps(s)],'packet_sha256':p['packet_sha256'],'max_bytes':contract.MAX_BYTES,'references':[ctx.reference(row)]}
    response=c.post('/v1/ai-opportunity-handoff/knowledge-context',json=req,headers=h)
    assert response.status_code==200,response.text
    assert json.loads(response.json()['context_json'])['reviewed_knowledge']==[row]
    assert c.post('/v1/ai-opportunity-handoff/knowledge-context',json={**req,'references':[ctx.reference(hidden)]},headers=h).status_code==422
    script=c.get('/ai-implementation-combined-authoring.js')
    assert script.status_code==200 and 'combinedAuthoring' in script.text
