"""Exercise existing local source-check retention and cohort router together."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server import project_source_checks as checks
from server import project_source_cohort_routes as routes
from tests.test_project_source_checks import prepared, request, RESOURCE


def test_retained_cohort_and_owner_only_http(prepared, monkeypatch):
    _,_,idea,project,calls=prepared
    iid,pid=idea['idea_id'],project['project_id']
    content,meta=checks.export_check(iid,pid,request(idea))
    before=checks.exports(iid,pid)
    assert len(calls)==1
    body={'expected_idea_revision':idea['revision'],'cases':[
        {'case_id':'case_1','terminal_activity_id':'review','expected_operation':'github_pr_merged',
         'resource_ref':RESOURCE,'document_sha256':meta['document_sha256']},
        {'case_id':'case_2','terminal_activity_id':'review','expected_operation':'github_pr_merged',
         'resource_ref':None,'document_sha256':None}], 'documents':[content.decode('utf-8')]}
    monkeypatch.setattr(routes,'dashboard_session_valid',lambda v:v=='owner')
    app=FastAPI();app.include_router(routes.router)
    url='/v1/automation-pipeline/'+iid+'/projects/'+pid+'/source-check-cohort-review'
    with TestClient(app) as c:
        for h in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
            assert c.post(url,json=body,headers=h).status_code==403
        h={'Authorization':'OWG-Session owner'}
        result=c.post(url,json=body,headers=h)
        assert result.status_code==200,result.text
        assert result.headers['cache-control']=='no-store'
        r=result.json()
        assert r['case_count']==2 and r['historical_operation_reported_verified_count']==1
        assert r['missing_document_count']==1 and r['execution_authorized'] is False
        assert RESOURCE not in result.text
        assert c.post(url,json={**body,'documents':[content.decode()+' ']},headers=h).status_code==422
        assert c.post(url,json={**body,'expected_idea_revision':idea['revision']+1},headers=h).status_code==422
        assert c.post(url,content=b'{"cases":[],"cases":[]}',headers={**h,'Content-Type':'application/json'}).status_code==422
    assert len(calls)==1 and checks.exports(iid,pid)==before
