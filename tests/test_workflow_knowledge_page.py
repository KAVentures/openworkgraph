"""Persisted owner-scoped cursor regression using the existing KnowledgeStore."""
import pytest
from fastapi import HTTPException
from gateway.db import GatewayDB
from shared.workflow_knowledge import KnowledgeStore, KnowledgeWrite
from server import workflow_evidence_routes as routes


def test_paginated_latest_knowledge_and_legacy_exact_read(tmp_path, monkeypatch):
    store = KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "paged.db"}'))
    monkeypatch.setattr(routes, '_knowledge', store)
    for i in range(105):
        store.save('local', KnowledgeWrite(workflow_id=f'process_{i:03d}', title='Procedure',
            procedure='Reviewed', source_client='test', user_confirmed=True))
    store.save('other', KnowledgeWrite(workflow_id='process_private', title='Other owner',
        procedure='Not local', source_client='test', user_confirmed=True))
    store.save('local', KnowledgeWrite(workflow_id='process_001', title='Revised',
        procedure='Reviewed revision', source_client='test', expected_revision=1, user_confirmed=True))
    first = routes.get_workflow_knowledge(limit=100, after='')
    assert first['returned']==100 and first['has_more'] and first['next_after']=='process_099'
    assert first['workflows'][1]['revision']==2
    second = routes.get_workflow_knowledge(limit=100, after=first['next_after'])
    assert second['returned']==5 and not second['has_more'] and second['next_after'] is None
    ids=[r['workflow_id'] for r in first['workflows']+second['workflows']]
    assert ids==[f'process_{i:03d}' for i in range(105)]
    exact=routes.get_workflow_knowledge(workflow_id='process_001')
    assert exact==store.list('local','process_001',50)
    for args in [{'after':'bad/id'},{'after':'','limit':101},{'after':'','workflow_id':'process_001'}]:
        with pytest.raises(HTTPException) as exc:
            routes.get_workflow_knowledge(**args)
        assert exc.value.status_code==422
