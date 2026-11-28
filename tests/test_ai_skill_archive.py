from copy import deepcopy
from io import BytesIO
import json
from zipfile import ZipFile
import pytest
from shared.ai_skill_archive import archive, preview, canonical, digest


def prepare(sources, proposal, draft):
    files = {'claude/example/SKILL.md':draft['instructions'].encode(), 'CHATGPT_INSTRUCTIONS.md':draft['instructions'].encode()}
    manifest = {'format':'openworkgraph.ai-shadow-skill-package.v1','name':'example','mode':'shadow_only',
                'files':{k:{'sha256':digest(v),'bytes':len(v)} for k,v in files.items()},
                'draft_sha256':digest(canonical(draft)), 'proposal_sha256':digest(canonical(proposal)),
                'source_sha256':digest(canonical(sources)),
                **dict.fromkeys(('semantic_correctness_verified','deployment_verified','client_permissions_verified','execution_authorized'),False)}
    return files, {**manifest,'package_sha256':digest(canonical(manifest))}


def request():
    return {'sources':[{'private':'PRIVATE'}],'proposal':{'example':True},'draft':{'instructions':'Reviewed instructions ä'}}


def confirm(p):
    return {'draft_request':p,'expected_package_sha256':preview(p,prepare=prepare)['manifest']['package_sha256'],'confirm_shadow_download':True}


def test_exact_deterministic_zip_and_rendered_preview():
    p=request();v=preview(p,prepare=prepare);q=confirm(p)
    raw,m=archive(q,prepare=prepare)
    assert archive(q,prepare=prepare)==(raw,m)
    assert m==v['manifest'] and not v['execution_authorized']
    with ZipFile(BytesIO(raw)) as z:
        assert z.namelist()==sorted([*v['rendered_files'],'manifest.json'])
        assert json.loads(z.read('manifest.json'))==m
        for path,text in v['rendered_files'].items():
            assert z.read(path)==text.encode() and digest(z.read(path))==m['files'][path]['sha256']
            assert z.getinfo(path).external_attr>>16==0o100600
    assert b'PRIVATE' not in raw


def test_refuses_stale_preview_and_unconfirmed_download():
    p=request();q=confirm(p)
    for key,value in [('sources',[{}]),('proposal',{}),('draft',{'instructions':'changed'})]:
        changed=deepcopy(q);changed['draft_request'][key]=value
        with pytest.raises(ValueError,match='preview changed'):archive(changed,prepare=prepare)
    for value in (False,1,None):
        with pytest.raises(ValueError,match='confirmation'):archive({**q,'confirm_shadow_download':value},prepare=prepare)


def test_invalid_inventory_manifest_and_size():
    p=request()
    for mode in ('extra','unsafe','permission','hash','file','size'):
        def invalid(*args):
            files,m=prepare(*args)
            if mode=='extra':files['extra']=b'x'
            elif mode=='unsafe':m['name']='../escape'
            elif mode=='permission':m['execution_authorized']=True
            elif mode=='hash':m['package_sha256']='a'*64
            elif mode=='file':files['CHATGPT_INSTRUCTIONS.md']=b'changed'
            else:
                files['CHATGPT_INSTRUCTIONS.md']=b'x'*(256*1024+1)
                m['files']['CHATGPT_INSTRUCTIONS.md']={'sha256':digest(files['CHATGPT_INSTRUCTIONS.md']),'bytes':len(files['CHATGPT_INSTRUCTIONS.md'])}
                m['package_sha256']=digest(canonical({k:v for k,v in m.items() if k!='package_sha256'}))
            return files,m
        with pytest.raises(ValueError):preview(p,prepare=invalid)
