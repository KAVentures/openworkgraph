import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import ai_implementation_batch_routes as batch_routes
from server import ai_implementation_compat_routes as routes
from shared import ai_implementation_review as contract
from test_ai_implementation_review import proposal
from test_ai_implementation_compat import fixture

BASE = '/v1/ai-opportunity-handoff/compat-'
HEADERS = {'Authorization':'OWG-Session owner'}


def setup(monkeypatch):
    monkeypatch.setattr(batch_routes, 'dashboard_session_valid', lambda v: v == 'owner')
    app = FastAPI(); app.include_router(routes.router)
    s, pin, old, review = fixture()
    return TestClient(app), {'source_json':json.dumps(s), 'packet_sha256':contract.prepare([s])['packet_sha256'],
        'evidence_sha256':pin, 'sectioned_review_sha256':review, 'sectioned_proposal_json':json.dumps(old)}, proposal([s])


def test_exact_prepare_and_review(monkeypatch):
    c,p,new = setup(monkeypatch)
    r=c.post(BASE+'prepare',json=p,headers=HEADERS);assert r.status_code==200,r.text
    data=r.json();h=json.loads(data['handoff_json'])
    assert r.headers['cache-control']=='no-store' and data['handoff_json']==contract._json(h).decode()
    assert h['packet_sha256']==p['packet_sha256'] and len(h['section_reference_map'])==12
    req={**p,'handoff_sha256':data['handoff_sha256'],'integrated_proposal_json':json.dumps(new)}
    r=c.post(BASE+'review',json=req,headers=HEADERS);assert r.status_code==200,r.text
    data=r.json();report=json.loads(data['report_json'])
    assert data['proposal_json']==contract._json(new).decode()
    assert report['integrated_proposal_sha256']==contract._digest(new)
    assert report['retained_distinct_citation_count']==1 and not data['portfolio_written']
    for change in ({'packet_sha256':'0'*64},{'handoff_sha256':'0'*64},{'sectioned_review_sha256':'0'*64},
                   {'integrated_proposal_json':'{"x":1,"x":2}'}):
        assert c.post(BASE+'review',json={**req,**change},headers=HEADERS).status_code==422


def test_auth_and_bounds(monkeypatch):
    c,p,new=setup(monkeypatch)
    for h in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
        for path in ('prepare','review'):assert c.post(BASE+path,json=p,headers=h).status_code==403
    for change in ({'source_json':'{}'},{'sectioned_proposal_json':'[NaN]'},
                   {'sectioned_proposal_json':' '*(routes.MAX_PROPOSAL+1)},{'evidence_sha256':'0'*64}):
        assert c.post(BASE+'prepare',json={**p,**change},headers=HEADERS).status_code==422
    monkeypatch.setattr(batch_routes,'MAX_REQUEST',8)
    assert c.post(BASE+'prepare',json=p,headers=HEADERS).status_code==413
