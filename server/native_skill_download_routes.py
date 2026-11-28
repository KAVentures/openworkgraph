"""Owner-only preview and download of a native shadow skill archive."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from shared import ai_skill_archive as skill
from .local_auth import dashboard_session_valid

router = APIRouter()
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'native_skill_download.js'
MAX_REQUEST_BYTES = 12 * 1024 * 1024


def _require_dashboard(request):
    auth = request.headers.get('authorization', '')
    if not auth.lower().startswith('owg-session ') or not dashboard_session_valid(auth[len('owg-session '):].strip()):
        raise HTTPException(403, 'Local dashboard session required')


def decode(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate key')
            result[k] = v
        return result

    def constant(_):
        raise ValueError('nonfinite JSON')

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    skill.canonical(value)
    return value


async def body(request):
    _require_dashboard(request)
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'JSON request required')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, 'Selected documents exceed request budget')
        raw.extend(chunk)
    _require_dashboard(request)
    try:
        return decode(raw.decode('utf-8'))
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Invalid JSON request') from None


def draft_request(value):
    if (not isinstance(value, dict) or set(value) != {'sources', 'proposal', 'draft'}
            or not isinstance(value['sources'], list) or not 1 <= len(value['sources']) <= 8
            or not all(isinstance(s, str) for s in value['sources'])
            or not isinstance(value['proposal'], str) or not isinstance(value['draft'], str)):
        raise ValueError('Select original JSON files and reviewed draft')
    return {'sources': [decode(s) for s in value['sources']], 'proposal': decode(value['proposal']),
            'draft': decode(value['draft'])}


@router.get('/native-skill-download.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


@router.post('/v1/native-skill-download/preview')
async def preview(request: Request):
    payload = await body(request)
    try:
        result = await run_in_threadpool(skill.preview, draft_request(payload))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Skill preview refused; check the original evidence, proposal and reviewed draft') from None
    _require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.post('/v1/native-skill-download/archive')
async def archive(request: Request):
    payload = await body(request)
    try:
        if not isinstance(payload, dict) or set(payload) != {'draft_request','expected_package_sha256','confirm_shadow_download'}:
            raise ValueError('Exact preview and confirmation required')
        content, manifest = await run_in_threadpool(skill.archive,
            {**payload, 'draft_request': draft_request(payload['draft_request'])})
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise HTTPException(422, 'Download refused; review the exact package again and explicitly confirm') from None
    _require_dashboard(request)
    return Response(content, media_type='application/zip', headers={
        'Cache-Control': 'no-store', 'Content-Disposition': 'attachment; filename="owg-shadow-skill.zip"',
        'X-OWG-Package-SHA256': manifest['package_sha256'], 'X-OWG-Archive-SHA256': skill.digest(content)})
