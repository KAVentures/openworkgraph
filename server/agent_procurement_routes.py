"""Local, ephemeral agent procurement reviews of exact saved process revisions."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import agent_procurement as single
from shared import agent_procurement_trials as trials
from .local_auth import dashboard_session_valid
from .local_process_catalog import catalog

router = APIRouter()
# Two 8 MiB input documents plus JSON string escaping. Limit the incoming
# stream and separately enforce each decoded document's original byte limit.
MAX_ENVELOPE = 2 * single.MAX_BYTES * 2 + 65536


def _require_dashboard(request):
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required for agent procurement")


async def _envelope(request):
    if str(request.headers.get("content-type") or "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Procurement request must be JSON")
    body=bytearray()
    async for part in request.stream():
        if len(body)+len(part)>MAX_ENVELOPE:
            raise HTTPException(413, "Procurement request exceeds the supported size")
        body.extend(part)
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError("duplicate key")
            out[key]=value
        return out
    def invalid(_):
        raise ValueError("nonfinite number")
    try:
        return json.loads(body,object_pairs_hook=unique,parse_constant=invalid)
    except (ValueError,UnicodeError,RecursionError):
        raise HTTPException(422,"Invalid procurement request JSON") from None


def _inspect(operation, workflow_id, revision, payload):
    keys={"document","plan"} if operation=="review" else {"document"}
    if not isinstance(payload,dict) or set(payload)!=keys or any(not isinstance(v,str) for v in payload.values()):
        raise HTTPException(422,"Supply the selected procurement JSON document(s) as text")
    try:
        packet=catalog.export(workflow_id,revision)
        document=single.loads(payload["document"])
        if operation=="compare":
            result=single.compare(packet,document)
        elif operation=="freeze":
            result=trials.freeze(packet,document)
        else:
            result=trials.review(packet,single.loads(payload["plan"]),document)
        latest=catalog.get(workflow_id)["revision"]
    except LookupError:
        raise HTTPException(404,"Saved process revision not found") from None
    except (ValueError,KeyError,TypeError,RecursionError,OverflowError):
        # Validation errors can contain submitted text. Never echo it into the
        # local UI, logs or model context.
        raise HTTPException(422,"Invalid or incompatible procurement documents for this exact saved revision") from None
    return {
        "result":result,"workflow_id":workflow_id,"revision":revision,
        "latest_revision_at_inspection":latest,
        "references_latest_revision_at_inspection":latest==revision,
        "result_persisted":False,"execution_authorized":False,
    }


@router.post("/v1/process-catalog/procurement/{operation}")
async def inspect_procurement(request: Request, operation: Literal["compare","freeze","review"],
                              workflow_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,80}$"),
                              revision: int = Query(ge=1,le=2**31-1)):
    _require_dashboard(request)
    payload=await _envelope(request)
    _require_dashboard(request)
    result=await run_in_threadpool(_inspect,operation,workflow_id,revision,payload)
    return JSONResponse(result,headers={"Cache-Control":"no-store"})
