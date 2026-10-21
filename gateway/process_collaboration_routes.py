"""Scoped organization process endpoints; no service-token or evidence-scope fallback."""
from __future__ import annotations

import json
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path as APIPath, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from shared.process_catalog import ProcessCatalog, process_bpmn, process_document
from .process_collaboration import (
    OrganizationProcessStore, ProcessCollaborationError, SharedProcessWrite,
    ProcessAccessWrite, ProcessReviewWrite, ProcessArchiveWrite, ProcessDeleteWrite, namespace,
)

_MODEL = TypeVar('_MODEL', bound=BaseModel)
_ID = r'^[A-Za-z0-9_-]{1,80}$'


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('nonstandard JSON constant')


async def _body(request: Request, model: type[_MODEL]) -> _MODEL:
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'application/json required')
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 256 * 1024:
            raise HTTPException(413, 'Shared process request exceeds 256 KiB')
        data.extend(chunk)
    try:
        return model.model_validate(json.loads(data, object_pairs_hook=_object, parse_constant=_invalid_constant))
    except ValidationError as exc:
        # Root graph validators emit fixed product messages. Surface those to
        # help editors repair connectivity without echoing input or field paths.
        graph_errors = [error['msg'].removeprefix('Value error, ') for error in exc.errors(
            include_input=False, include_context=False, include_url=False)
            if error['type'] == 'value_error' and error['loc'] == ('process_model',)]
        if graph_errors:
            raise HTTPException(422, 'Invalid process graph: ' + graph_errors[0]) from None
        raise HTTPException(422, 'Invalid shared process JSON or review fields') from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, 'Invalid shared process JSON or review fields') from None
    finally:
        data.clear()


def _response(operation, *args, **kwargs):
    try:
        return JSONResponse(operation(*args, **kwargs), headers={'Cache-Control': 'no-store'})
    except ProcessCollaborationError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except ValueError:
        raise HTTPException(409, 'Process revision changed; reload and review the current design') from None


def build_process_router(store: OrganizationProcessStore, human) -> APIRouter:
    router = APIRouter()

    @router.get('/processes', include_in_schema=False)
    def portal():
        from .html_pages import render_page
        return render_page('processes.html', config={})

    @router.get('/process-graph.js', include_in_schema=False)
    def graph_script():
        from pathlib import Path
        return Response(Path(__file__).with_name('process_graph.js').read_text(encoding='utf-8'),
                        media_type='application/javascript', headers={
                            'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'})

    @router.get('/v1/human/processes')
    def inventory(after: str = Query(default='', max_length=80), limit: int = Query(default=20, ge=1, le=100),
                  include_archived: bool = False, p=Depends(human)):
        return _response(store.inventory, p, after=after, limit=limit, include_archived=include_archived)

    @router.post('/v1/human/processes')
    async def publish(request: Request, p=Depends(human)):
        # Reject missing permissions before parsing any supplied model content.
        for scope in ('org:process:read', 'org:process:write'):
            if scope not in p.scopes:
                raise HTTPException(403, 'Missing process permission: ' + scope)
        return await run_in_threadpool(_response, store.save, p, await _body(request, SharedProcessWrite))

    @router.get('/v1/human/processes/{workflow_id}')
    def detail(workflow_id: str = APIPath(pattern=_ID),
               revision: int | None = Query(default=None, ge=1), p=Depends(human)):
        return _response(store.detail, p, workflow_id, revision=revision)

    @router.post('/v1/human/processes/{workflow_id}')
    async def update(request: Request, workflow_id: str = APIPath(pattern=_ID), p=Depends(human)):
        return await run_in_threadpool(_response, store.save, p, await _body(request, SharedProcessWrite), workflow_id)

    @router.post('/v1/human/processes/{workflow_id}/access')
    async def access(request: Request, workflow_id: str = APIPath(pattern=_ID), p=Depends(human)):
        return await run_in_threadpool(_response, store.set_access, p, workflow_id, await _body(request, ProcessAccessWrite))

    @router.post('/v1/human/processes/{workflow_id}/reviews')
    async def review(request: Request, workflow_id: str = APIPath(pattern=_ID), p=Depends(human)):
        return await run_in_threadpool(_response, store.review, p, workflow_id, await _body(request, ProcessReviewWrite))

    @router.post('/v1/human/processes/{workflow_id}/archive')
    async def archive(request: Request, workflow_id: str = APIPath(pattern=_ID), p=Depends(human)):
        return await run_in_threadpool(_response, store.archive, p, workflow_id, await _body(request, ProcessArchiveWrite))

    @router.post('/v1/human/processes/{workflow_id}/forget')
    async def forget(request: Request, workflow_id: str = APIPath(pattern=_ID), p=Depends(human)):
        return await run_in_threadpool(_response, store.delete, p, workflow_id, await _body(request, ProcessDeleteWrite))

    @router.get('/v1/human/processes/{workflow_id}/history')
    def history(workflow_id: str = APIPath(pattern=_ID),
                before_revision: int | None = Query(default=None, ge=1), limit: int = Query(default=20, ge=1, le=100), p=Depends(human)):
        try:
            store.detail(p, workflow_id)
            result = store.knowledge.history(namespace(p.organization_id), workflow_id, before_revision=before_revision, limit=limit)
            # Preserve designs, not source traces. Evidence refs are empty at publication.
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except ProcessCollaborationError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @router.get('/v1/human/processes/{workflow_id}/export')
    def export(workflow_id: str = APIPath(pattern=_ID), revision: int = Query(ge=1),
               format: str = Query(default='json', pattern='^(json|bpmn|pdd)$'), p=Depends(human)):
        try:
            detail = store.detail(p, workflow_id, revision=revision)
            packet = ProcessCatalog(store.knowledge, namespace(p.organization_id)).export(workflow_id, revision)
        except ProcessCollaborationError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except LookupError:
            raise HTTPException(404, 'Shared process revision not found') from None
        packet['collaboration_review'] = detail['review']
        packet['organization_id'] = p.organization_id
        packet['owner_actor_id_at_export'] = detail['owner_actor_id']
        packet['revision_author_actor_id'] = detail['author_actor_id']
        store.db.audit(organization_id=p.organization_id, principal_id=p.audit_id,
                       action='process.exported', details={'workflow_id': workflow_id, 'revision': revision, 'format': format})
        packet['shared_design_is_business_policy'] = False
        headers = {'Cache-Control': 'no-store', 'Content-Disposition':
                   f'attachment; filename="owg-shared-process-r{revision}.{ {"json":"json","bpmn":"bpmn","pdd":"txt"}[format]}"'}
        if format == 'json':
            return JSONResponse(packet, headers=headers)
        return Response(process_bpmn(packet) if format == 'bpmn' else process_document(packet) + '\nOrganization design review: ' + detail['review']['status'] + '\nThis review is not business-policy approval or execution authority.\n',
                        media_type='application/xml' if format == 'bpmn' else 'text/plain', headers=headers)

    return router
