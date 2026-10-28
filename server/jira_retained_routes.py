"""Explicit local dashboard capabilities for selected Jira retention."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import jira_retained_profiles as profiles
from .jira_retained_source import JiraIngestionError, strict_json
from .local_auth import dashboard_session_valid


def _require_dashboard(request: Request):
    auth = request.headers.get('authorization', '')
    if not auth.lower().startswith('owg-session ') or not dashboard_session_valid(auth[12:].strip()):
        raise HTTPException(403, 'Explicit local dashboard session required for Jira retention')


router = APIRouter(prefix='/v1/event-log-sources/jira', dependencies=[Depends(_require_dashboard)])


async def _body(request, keys, alternatives=()):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON source request required')
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > 4096:
                raise HTTPException(413, 'Jira profile request too large')
            body.extend(chunk)
        value = strict_json(body)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, 'Invalid Jira profile JSON') from None
    finally:
        body.clear()
    if not isinstance(value, dict) or set(value) not in (set(keys), *(set(k) for k in alternatives)):
        raise HTTPException(422, 'Exact Jira profile fields required')
    return value


async def _respond(operation, *args, **kwargs):
    try:
        value = await run_in_threadpool(operation, *args, **kwargs)
        return JSONResponse(value, headers={'Cache-Control': 'no-store'})
    except JiraIngestionError as error:
        raise HTTPException(422, str(error), headers={'Cache-Control': 'no-store'}) from None


@router.get('')
async def list_profiles():
    return await _respond(profiles.status)


@router.post('')
async def create_profile(request: Request):
    confirmations = ('confirm_reads', 'confirm_retention', 'confirm_status_labels')
    body = await _body(request, ('resource_refs', *confirmations), alternatives=[('issue_keys', *confirmations)])
    if 'issue_keys' in body:
        return await _respond(profiles.create_native, body.pop('issue_keys'), **body)
    return await _respond(profiles.create, body.pop('resource_refs'), **body)


@router.post('/{profile_id}/run')
async def run_profile(profile_id: str, request: Request):
    body = await _body(request, ('confirm_reads',))
    return await _respond(profiles.run, profile_id, **body)


@router.post('/{profile_id}/enabled')
async def enable_profile(profile_id: str, request: Request):
    body = await _body(request, ('enabled', 'confirmed'))
    if type(body['confirmed']) is not bool:
        raise HTTPException(422, 'Boolean confirmation required')
    return await _respond(profiles.change, profile_id, **body)


@router.delete('/{profile_id}')
async def disconnect_profile(profile_id: str):
    return await _respond(profiles.change, profile_id, disconnect=True)
