"""Illustrated documents bind to existing saved graphs, without linearizing them."""
import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree as ET

import pytest
from gateway.db import GatewayDB
from shared.process_catalog import CatalogWrite, ProcessCatalog
from shared.workflow_knowledge import KnowledgeStore
from server import process_pdd_package as pdd
from test_process_catalog import model, branched_model
from test_parallel_process_model import parallel_model
from test_process_pdd_package import _files, _png

ROOT = Path(__file__).resolve().parents[1]


def source(tmp_path, graph):
    catalog = ProcessCatalog(KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "pdd.db"}')), 'owner')
    saved = catalog.save(CatalogWrite(title='Saved <design>', process_model=graph, user_confirmed=True))
    packet = catalog.export(saved['workflow_id'], saved['revision'])
    reference = {k: packet[k] for k in ('workflow_id', 'revision', 'content_sha256')}
    return packet, {'format': pdd.FORMAT, 'process_reference': reference}


@pytest.mark.parametrize('factory', [model, branched_model, parallel_model])
def test_documents_preserve_the_saved_graph_and_attach_images_to_real_node_ids(tmp_path, factory):
    graph = factory()
    packet, request = source(tmp_path, graph)
    node = graph.activities[1]
    request.update(screenshots=[{'step_id': node.id, 'image_base64': _png(), 'masked_regions': 1}],
                   screenshots_reviewed_and_redacted_by_user=True,
                   narrative={'text': 'A proposed description', 'ai_drafted': True})
    archive = pdd.build_pdd_package(request, packet)
    files = _files(archive)
    assert json.loads(files['process.json']) == packet
    assert f'images/{node.id}_1.png' in files
    manifest = json.loads(files['manifest.json'])
    assert manifest['process_reference'] == request['process_reference']
    assert manifest['process_steps'] == len(graph.activities)
    assert not manifest['stored_by_owg'] and not manifest['execution_authorized']
    assert not manifest['business_outcomes_verified']
    xml = ET.fromstring(files['process.bpmn'])
    ns = {'b': 'http://www.omg.org/spec/BPMN/20100524/MODEL'}
    edges = {(e.attrib['sourceRef'].removeprefix('Activity_'), e.attrib['targetRef'].removeprefix('Activity_'))
             for e in xml.findall('.//b:sequenceFlow', ns)}
    assert edges == {(e.source, e.target) for e in graph.transitions}
    assert xml.find('b:process', ns).attrib['isExecutable'] == 'false'
    if factory is parallel_model:
        assert len(xml.findall('.//b:parallelGateway', ns)) == 2
    md = files['PDD.md'].decode()
    assert 'attachment index, not an execution sequence' in md
    assert 'AI-drafted text, not reviewed or verified' in md
    assert 'Saved &lt;design&gt;' in files['PDD.html'].decode()
    assert '<design>' not in files['PDD.html'].decode()
    for edge in graph.transitions:
        assert f'{edge.source} → {edge.target}' in files['process.txt'].decode()
    assert archive == pdd.build_pdd_package(request, packet)


@pytest.mark.parametrize('change', [
    {'revision': True}, {'revision': 0}, {'revision': 2**63}, {'content_sha256': 'f' * 63},
    {'workflow_id': '../other'}, {'extra': 1},
])
def test_document_references_are_strict(tmp_path, change):
    packet, request = source(tmp_path, model())
    request['process_reference'].update(change)
    with pytest.raises(pdd.PddPackageError):
        pdd.build_pdd_package(request, packet)


def test_saved_documents_refuse_unresolved_mismatched_or_dual_sources(tmp_path):
    packet, request = source(tmp_path, model())
    with pytest.raises(pdd.PddPackageError):pdd.build_pdd_package(request)
    with pytest.raises(pdd.PddPackageError):pdd.build_pdd_package(request, {**packet, 'revision': 2})
    with pytest.raises(pdd.PddPackageError):pdd.build_pdd_package({**request, 'design': {}}, packet)
    with pytest.raises(pdd.PddPackageError):pdd.build_pdd_package(request, {**packet, 'execution_authorization': True})
    with pytest.raises(pdd.PddPackageError):
        pdd.build_pdd_package({**request, 'screenshots': [{'step_id': '../image', 'image_base64': _png()}],
                              'screenshots_reviewed_and_redacted_by_user': True}, packet)


def test_real_catalog_reference_history_auth_deletion_and_export_race(tmp_path):
    code = r'''
import json
from io import BytesIO
from zipfile import ZipFile
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, db
from server import process_pdd_package as package
from server.workflow_evidence_routes import _catalog
from shared.process_catalog import CatalogWrite
from tests.test_parallel_process_model import parallel_model
with TestClient(app) as client:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 made=client.post('/v1/process-catalog',headers=h,json={'title':'Parallel review','process_model':parallel_model().model_dump(),'user_confirmed':True})
 assert made.status_code==200,made.text
 saved=made.json();ref={k:saved[k] for k in ('workflow_id','revision','content_sha256')}
 body={'format':package.FORMAT,'process_reference':ref};url='/v1/process-design/pdd-package'
 assert client.post(url,json=body).status_code==401
 assert client.post(url,json=body,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code==403
 bad=client.post(url,json={**body,'process_reference':{**ref,'content_sha256':'0'*64}},headers=h)
 assert bad.status_code==409,bad.text
 # A newer revision does not retarget this explicit historical reference.
 newer=_catalog.save(CatalogWrite(workflow_id=ref['workflow_id'],expected_revision=1,title='Revision two',process_model=parallel_model(),user_confirmed=True))
 ok=client.post(url,json=body,headers=h)
 assert ok.status_code==200,ok.text
 assert ok.headers['cache-control']=='no-store'
 files=ZipFile(BytesIO(ok.content));manifest=json.loads(files.read('manifest.json'))
 assert manifest['process_reference']==ref and json.loads(files.read('process.json'))['revision']==1
 assert len(_catalog.store.history('local',ref['workflow_id'])['workflows'])==2
 duplicate='{"format":"'+package.FORMAT+'","process_reference":'+json.dumps(ref)+',"process_reference":'+json.dumps(ref)+'}'
 assert client.post(url,content=duplicate,headers={**h,'Content-Type':'application/json'}).status_code==422
 original=package.build_pdd_package
 def build_and_delete(*args):
  archive=original(*args);_catalog.store.forget('local',ref['workflow_id']);return archive
 package.build_pdd_package=build_and_delete
 raced=client.post(url,json=body,headers=h)
 assert raced.status_code==404,raced.text
 package.build_pdd_package=original
 assert client.post(url,json=body,headers=h).status_code==404
 with db.connect() as conn:assert conn.execute('SELECT count(*) FROM events').fetchone()[0]==0
'''
    env={**os.environ, 'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),
         'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'), 'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json'),
         'PYTHONPATH':str(ROOT)}
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
