"""Read-only forecast reconciliation against existing retained quality-benefit data."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from pathlib import Path
from fractions import Fraction
from starlette.concurrency import run_in_threadpool

from shared import ai_implementation_batch as batch
from shared import ai_implementation_planning as planning
from shared import ai_implementation_portfolio_link as link
from shared import ai_implementation_forecast_review as forecast
from shared import ai_implementation_review as contract
from . import ai_implementation_batch_routes as batch_routes
from . import ai_implementation_portfolio_routes as portfolio_routes

router = APIRouter()
FIELDS = portfolio_routes.FIELDS | {'reconciliation_sha256','item_index','quality_report_json','comparability'}
MAX_QUALITY_BYTES = 256 * 1024
SCRIPT = Path(__file__).resolve().parents[1] / 'dashboard' / 'ai_implementation_forecast.js'


def quality_result(idea_id, assumptions):
    from . import automation_quality_benefit
    return automation_quality_benefit.analyze(idea_id, assumptions)


def same_json(a, b):
    # The existing browser download uses JSON.stringify: integer-valued floats
    # become JSON integers. Compare exact numeric values without tolerances;
    # never treat booleans as numbers or overlook different fields/arrays.
    if type(a) in (int, float) and type(b) in (int, float):
        return Fraction(str(a)) == Fraction(str(b))
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same_json(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same_json(x, y) for x, y in zip(a, b))
    return a == b


def documents(payload):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError('Exact forecast comparison inputs required')
    sources, proposals, plan, exported = (payload[k] for k in ('sources','proposals_json','plan_json','quality_report_json'))
    if (not isinstance(sources, list) or not 1 <= len(sources) <= contract.MAX_BUNDLES
            or not all(isinstance(s, str) for s in sources)
            or not isinstance(proposals, str) or not 0 < len(proposals.encode('utf-8')) <= batch.MAX_BATCH_BYTES
            or not isinstance(plan, str) or not 0 < len(plan.encode('utf-8')) <= planning.MAX_PLAN_BYTES
            or not isinstance(exported, str) or not 0 < len(exported.encode('utf-8')) <= MAX_QUALITY_BYTES):
        raise ValueError('Invalid source, proposal, plan or quality document size')
    args = ([batch_routes.decode(s) for s in sources], payload['packet_sha256'], batch_routes.decode(proposals),
            payload['batch_review_sha256'], batch_routes.decode(plan), payload['planning_review_sha256'])
    pin, i = payload['reconciliation_sha256'], payload['item_index']
    current = link.reconcile(*args, portfolio_routes.current_ideas())
    link.require(current['reconciliation_sha256'] == pin and type(i) is int and 0 <= i < current['item_count'],
                 'Portfolio snapshot or selection changed')
    row = current['items'][i]; saved = row['saved_idea']
    link.require(row['mapping_status'] == 'matched' and row['scenario_assessed'] and saved is not None
                 and saved['state'] == 'implemented', 'Select a matched implemented and assessed candidate')
    doc = batch_routes.decode(exported)
    link.require(isinstance(doc, dict) and doc.get('format') == forecast.QUALITY and doc.get('idea_id') == saved['idea_id']
                 and doc.get('idea_revision') == saved['revision'] and isinstance(doc.get('assumptions'), dict),
                 'Quality export does not match the saved idea revision')
    actual = quality_result(saved['idea_id'], doc['assumptions'])
    # Uploaded values are not accepted as measurements. The original engine
    # recomputes them from its own pinned retained periods and declared inputs.
    link.require(same_json(actual, doc), 'Quality export changed; recompute in the existing quality panel')
    result = forecast.compare(args, portfolio_routes.current_ideas(), pin, i, actual, doc['scenario_sha256'], payload['comparability'])
    return {'format':'openworkgraph.ai-implementation-forecast-result.v1',
            'report_json':contract._json(result).decode('utf-8'), 'forecast_review_sha256':result['forecast_review_sha256'],
            'portfolio_written':False, 'execution_authorized':False}


@router.get('/ai-implementation-forecast.js')
def script():
    return FileResponse(SCRIPT, media_type='application/javascript')


@router.post('/v1/ai-opportunity-handoff/planning-forecast-review')
async def compare(request: Request):
    payload = await batch_routes.body(request)
    try:
        result = await run_in_threadpool(documents, payload)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, ZeroDivisionError):
        raise HTTPException(422, 'Forecast review refused; refresh the exact portfolio and quality-benefit export') from None
    batch_routes.require_dashboard(request)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})
