"""Declared step responsibilities persist without becoming identity or grants."""
import hashlib
import json
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from shared.process_agent_project import build_runtime_spec,build_runtime_project,process_model_digest
from shared.process_catalog import CatalogWrite,ProcessCatalog,process_bpmn,process_document
from shared.process_model import ProcessModel
from shared.workflow_knowledge import KnowledgeStore
from gateway.db import GatewayDB
from gateway.process_collaboration import SharedProcessWrite,ProcessCollaborationError
from test_process_collaboration import store,person,grant,review


def legacy_model():
    return {'goal':'Review a selected case','baseline_source':'user_declared','baseline_path':['Review','Close'],
        'activities':[{'id':'review','label':'Review'},{'id':'close','label':'Close'}],
        'transitions':[{'source':'review','target':'close'}],'entry_activity_id':'review','terminal_activity_ids':['close']}


def packet(model):
    return {'format':'openworkgraph.versioned-process-export.v1','workflow_id':'legacy_process','revision':2,
            'content_sha256':'a'*64,'execution_authorization':False,'process_model':model}


def test_unspecified_responsibilities_preserve_pre_feature_model_and_spec_hashes():
    model=ProcessModel.model_validate(legacy_model())
    assert all('responsibility_label' not in row for row in model.model_dump()['activities'])
    assert process_model_digest(model.model_dump())=='84d86912df27420eac5345265b92ec04935b2fe972a978d69299ac2f6977d1a1'
    spec=build_runtime_spec(packet(model.model_dump()))
    assert hashlib.sha256(json.dumps(spec,sort_keys=True,allow_nan=False).encode()).hexdigest()=='e5341c23cd231f23aaf626aabf0fb8fae0d29a6fba05e5cc7111d8f7e4e6dcd3'
    model.activities[0].responsibility_label=''
    assert process_model_digest(model.model_dump())==spec['process_model_sha256']


def test_responsibility_is_bounded_single_line_metadata_and_cannot_carry_grants():
    for value in ('a'*121,'Finance\nManager','Finance\x00Manager','Finance\ud800Manager'):
        data=legacy_model();data['activities'][0]['responsibility_label']=value
        with pytest.raises(ValidationError):ProcessModel.model_validate(data)
    for key in ('responsibility_verified','execution_authorized','review_permission'):
        data=legacy_model();data['activities'][0][key]=True
        with pytest.raises(ValidationError):ProcessModel.model_validate(data)
    data=legacy_model();data['activities'][0]['responsibility_label']='  Finance manager  '
    model=ProcessModel.model_validate(data)
    assert model.activities[0].responsibility_label=='Finance manager'
    assert model.execution_authorized is False and model.outcomes_verified is False


def test_labels_are_versioned_and_export_as_data_with_safe_xml_and_runtime_pinning(tmp_path):
    catalog=ProcessCatalog(KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "roles.db"}')),'alice')
    first=catalog.save(CatalogWrite(title='Review case',process_model=legacy_model(),user_confirmed=True))
    data=legacy_model();data['activities'][0]['responsibility_label']='Finance <review> & operations'
    second=catalog.save(CatalogWrite(workflow_id=first['workflow_id'],expected_revision=1,title='Review case',
                                    process_model=data,user_confirmed=True))
    assert first['content_sha256']!=second['content_sha256']
    assert 'responsibility_label' not in catalog.get(first['workflow_id'],1)['process_model']['activities'][0]
    exported=catalog.export(first['workflow_id'],2)
    root=ET.fromstring(process_bpmn(exported));node=root.find('.//*[@id="Activity_review"]')
    assert 'User-declared responsibility: Finance <review> & operations' in ''.join(node.itertext())
    assert 'not verified identity' in ''.join(node.itertext())
    document=process_document(exported)
    assert 'Declared responsibility: Finance <review> & operations' in document
    assert 'not verified identity or permission grants' in document
    with ZipFile(BytesIO(build_runtime_project(exported))) as archive:
        spec=json.loads(archive.read('workflow.spec.json'))
        assert spec['process_model']['activities'][0]['responsibility_label']=='Finance <review> & operations'
        assert spec['execution_authorization'] is False
        assert 'Finance <review>' not in archive.read('agent.py').decode()
        assert spec['process_model_sha256']!=process_model_digest(first['process_model'])


def test_declared_actor_labels_never_change_shared_permissions_and_edits_reset_review(store):
    data=legacy_model();data['activities'][0]['responsibility_label']='bob'
    request=SharedProcessWrite(title='Shared review',process_model=data,user_confirmed=True,organization_sharing_confirmed=True)
    saved=store.save(person(),request);wid=saved['process']['workflow_id']
    bob=store.detail(person('bob'),wid)
    assert bob['process']['process_model']['activities'][0]['responsibility_label']=='bob'
    assert bob['your_role']=='reader' and not bob['permissions']['review'] and not bob['permissions']['edit']
    with pytest.raises(ProcessCollaborationError):store.review(person('bob'),wid,review(saved['process']))
    grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}])
    store.review(person('bob'),wid,review(saved['process']))
    assert store.implementation_state(person(),wid,revision=1)['current_independent_design_review'] is True
    data['activities'][0]['responsibility_label']='Finance manager'
    changed=store.save(person(),SharedProcessWrite(title='Shared review',process_model=data,expected_revision=1,
        user_confirmed=True,organization_sharing_confirmed=True),wid)
    assert changed['process']['revision']==2 and changed['review']['status']=='needs_review'
    assert store.implementation_state(person(),wid,revision=1)['current_independent_design_review'] is False
    assert changed['process']['process_model']['execution_authorized'] is False
