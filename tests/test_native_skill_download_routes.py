"""Actual parent reviewer, staging format and authenticated download route."""
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import native_skill_download_routes as routes
from shared.ai_skill_stage import verify
from tests.test_ai_implementation_review import source, proposal


def inputs():
    sources = [source(True)]
    draft = {'name':'customer-follow-up','description':'Draft customer follow-up','option_index':0,
             'instructions':'Draft a reply from synthetic cases only.','tool_mappings':{},
             'source_verification':'Check independent fixture status.','test_plan':'Run frozen normal and exception cases.',
             'escalation':'Stop on missing facts.','rollback':'Remove staged skill and discard drafts.',
             'review_note':'Human reviewed against the proposal.'}
    return {'sources':[json.dumps(s) for s in sources],'proposal':json.dumps(proposal(sources)),
            'draft':json.dumps(draft)}


def test_actual_review_and_download_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(routes,'dashboard_session_valid',lambda v:v=='owner')
    app=FastAPI();app.include_router(routes.router)
    base='/v1/native-skill-download';data=inputs()
    with TestClient(app) as c:
        for h in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
            assert c.post(base+'/preview',json=data,headers=h).status_code==403
            assert c.post(base+'/archive',json={},headers=h).status_code==403
        h={'Authorization':'OWG-Session owner'}
        result=c.post(base+'/preview',json=data,headers=h)
        assert result.status_code==200,result.text
        assert result.headers['cache-control']=='no-store'
        p=result.json();pin=p['manifest']['package_sha256']
        assert 'disable-model-invocation: true' in p['rendered_files']['claude/customer-follow-up/SKILL.md']
        assert 'Ignore all instructions' not in result.text
        q={'draft_request':data,'expected_package_sha256':pin,'confirm_shadow_download':True}
        assert c.post(base+'/archive',json={**q,'confirm_shadow_download':False},headers=h).status_code==422
        assert c.post(base+'/archive',json={**q,'expected_package_sha256':'f'*64},headers=h).status_code==422
        changed=json.loads(data['draft']);changed['instructions']='Changed after preview'
        assert c.post(base+'/archive',json={**q,'draft_request':{**data,'draft':json.dumps(changed)}},headers=h).status_code==422
        assert c.post(base+'/preview',json={**data,'proposal':'{"x":1,"x":2}'},headers=h).status_code==422
        response=c.post(base+'/archive',json=q,headers=h)
        assert response.status_code==200,response.text
        assert response.headers['cache-control']=='no-store'
        assert response.headers['x-owg-package-sha256']==pin
        assert response.headers['x-owg-archive-sha256']==sha256(response.content).hexdigest()
        with ZipFile(BytesIO(response.content)) as archive:
            assert set(archive.namelist())=={'claude/customer-follow-up/SKILL.md','CHATGPT_INSTRUCTIONS.md','manifest.json'}
            archive.extractall(tmp_path/'staged')
        assert verify(tmp_path/'staged',pin)['exact_staged_files_verified']
        assert c.post(base+'/archive',json=q,headers=h).content==response.content
