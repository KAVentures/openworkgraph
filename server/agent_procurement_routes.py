"""Local, ephemeral agent procurement reviews of exact saved process revisions."""
from __future__ import annotations

import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import agent_procurement as single
from shared import agent_procurement_trials as trials
from shared import agent_procurement_selection as selection
from shared import agent_procurement_outcomes as outcomes
from shared import agent_procurement_operating_cost as economics
from shared import agent_procurement_case_robustness as robustness
from shared import agent_procurement_router_rehearsal as routing
from shared import agent_procurement_router_change as change
from shared import agent_procurement_router_pilot as pilot
from shared import agent_procurement_router_monitor as monitor
from . import agent_procurement_deployment as deployment
from . import agent_procurement_monitor_journal as journal
from . import agent_procurement_monitor_telemetry as telemetry
from . import agent_procurement_workforce_overview as workforce
from . import agent_procurement_workforce_allocation as allocation
from . import agent_procurement_finance as finance
from . import agent_procurement_corroboration as corroboration
from . import agent_procurement_source_outcome as source_outcome
from . import agent_procurement_source_finance as source_finance
from . import agent_procurement_workforce_exchange as exchange
from . import agent_procurement_workforce_signing as signing
from . import agent_procurement_workforce_trust as trust
from . import agent_procurement_workforce_inbox as inbox
from . import agent_procurement_workforce_dossier as dossier
from . import agent_procurement_workforce_audit_review as audit_review
from . import agent_procurement_workforce_remediation as remediation
from . import agent_procurement_workforce_remediation_tickets as remediation_tickets
from . import agent_procurement_workforce_jira_handoff as jira_handoff
from . import agent_procurement_monitor_schedule as monitor_schedule
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


async def _envelope(request, max_bytes=MAX_ENVELOPE):
    if str(request.headers.get("content-type") or "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Procurement request must be JSON")
    body=bytearray()
    async for part in request.stream():
        if len(body)+len(part)>max_bytes:
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
        document=single.loads(payload["document"])
        # Retained evidence must remain inspectable/removable even after the
        # associated reviewed process is deleted. These actions do not use a
        # process packet; writes that enable consent still check current scope.
        retained_only=operation in ("router-journal-list","router-journal-detail","router-journal-forget",
                                    "router-journal-schedule","router-producer-revoke")
        packet=None if retained_only else catalog.export(workflow_id,revision)
        if operation=="compare":
            result=single.compare(packet,document)
        elif operation=="freeze":
            result=trials.freeze(packet,document)
        elif operation=="review":
            result=trials.review(packet,single.loads(payload["plan"]),document)
        elif operation=="operating-cost":
            result=economics.operation(packet,document)
        elif operation=="case-robustness":
            result=robustness.operation(packet,document)
        elif operation in ("router-freeze","router-review"):
            if operation=="router-freeze" and catalog.get(workflow_id)["revision"]!=revision:
                raise HTTPException(409,"Open the latest saved process revision before freezing router rehearsal")
            result=routing.operation(packet,operation,document)
        elif operation in ("router-change-freeze","router-change-assess","router-change-decide"):
            if operation!="router-change-assess" and catalog.get(workflow_id)["revision"]!=revision:
                raise HTTPException(409,"Open the latest saved process revision before router change review")
            result=change.operation(packet,operation,document)
        elif operation in ("router-pilot-freeze","router-pilot-review","router-pilot-decide"):
            if operation!="router-pilot-review" and catalog.get(workflow_id)["revision"]!=revision:
                raise HTTPException(409,"Open latest saved process revision before pilot decision")
            result=pilot.operation(packet,operation,document)
        elif operation in ("router-monitor-freeze","router-monitor-review","router-monitor-decide"):
            if operation!="router-monitor-review" and catalog.get(workflow_id)["revision"]!=revision:
                raise HTTPException(409,"Open latest saved process revision before monitoring decision")
            result=monitor.operation(packet,operation,document)
        elif operation=="router-producer-grant":
            result=telemetry.grant(workflow_id,revision,document)
        elif operation=="router-producer-manifest":
            result=telemetry.manifest(workflow_id,revision,document)
        elif operation=="router-producer-revoke":
            result=telemetry.revoke(workflow_id,revision,document)
        elif operation=="router-journal-schedule":
            result=monitor_schedule.configure(workflow_id,revision,document)
        elif operation=="router-source-finance-check":
            result=source_finance.check(workflow_id,revision,document)
        elif operation=="router-source-outcome-bind":
            result=source_outcome.bind(workflow_id,revision,document)
        elif operation=="router-source-outcome-check":
            result=source_outcome.check(workflow_id,revision,document)
        elif operation=="router-corroboration-require":
            result=corroboration.require(workflow_id,revision,document)
        elif operation=="router-corroboration-manifest":
            result=corroboration.manifest(workflow_id,revision,document)
        elif operation=="router-corroboration-record":
            result=corroboration.record(workflow_id,revision,document)
        elif operation=="router-finance-reconcile":
            result=finance.record(workflow_id,revision,document)
        elif operation.startswith("router-journal-"):
            result=journal.operation(packet,workflow_id,revision,operation,document)
        elif operation in ("deployment-review","deployment-decide"):
            if catalog.get(workflow_id)["revision"]!=revision:
                raise HTTPException(409,"Open the latest saved process revision before deployment review")
            result=deployment.operation(packet,operation,document)
        elif operation in ("outcome-review","rollout-review"):
            keys={"decision_bundle","preflight","outcome_plan","observations"}
            if operation=="rollout-review":
                keys.add("request")
                if catalog.get(workflow_id)["revision"]!=revision:
                    raise HTTPException(409,"Open the latest saved process revision before rollout review")
            if not isinstance(document,dict) or set(document)!=keys:
                raise ValueError("exact outcome review bundle required")
            args=(packet,document["decision_bundle"],document["preflight"],document["outcome_plan"],document["observations"])
            result=(outcomes.review(*args) if operation=="outcome-review" else outcomes.decide(*args,document["request"]))
        else:
            if operation in ("nominate","decide") and catalog.get(workflow_id)["revision"]!=revision:
                raise HTTPException(409,"Open the latest saved process revision before nomination or decision")
            result=selection.operation(packet,operation,document)
        try:
            latest=catalog.get(workflow_id)["revision"]
        except LookupError:
            if not retained_only:raise
            latest=None
        if operation in ("nominate","decide","rollout-review","deployment-review","deployment-decide","router-freeze","router-change-freeze","router-change-decide","router-pilot-freeze","router-pilot-decide","router-monitor-freeze","router-monitor-decide") and latest!=revision:
            raise HTTPException(409,"Saved process revision changed during review; reopen the latest revision")
    except sqlite3.Error:
        raise HTTPException(503,"Local procurement storage temporarily unavailable") from None
    except journal.JournalError as exc:
        raise HTTPException(exc.status,str(exc)) from None
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
        "result_persisted":bool(result.get("result_persisted",False)),"execution_authorized":False,
    }


@router.post("/v1/process-catalog/procurement/{operation}")
async def inspect_procurement(request: Request, operation: Literal["compare","freeze","review","nominate","assess","decide","outcome-review","rollout-review","deployment-review","deployment-decide","operating-cost","case-robustness","router-freeze","router-review","router-change-freeze","router-change-assess","router-change-decide","router-pilot-freeze","router-pilot-review","router-pilot-decide","router-monitor-freeze","router-monitor-review","router-monitor-decide","router-journal-list","router-journal-append","router-journal-refresh","router-journal-schedule","router-journal-detail","router-journal-decide","router-journal-forget","router-producer-grant","router-producer-revoke","router-producer-manifest","router-finance-reconcile","router-corroboration-require","router-corroboration-manifest","router-corroboration-record","router-source-outcome-bind","router-source-outcome-check","router-source-finance-check"],
                              workflow_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,80}$"),
                              revision: int = Query(ge=1,le=2**31-1)):
    _require_dashboard(request)
    if operation.startswith(("router-producer-","router-corroboration-","router-source-outcome-","router-source-finance-")) and str(request.headers.get("X-OpenWorkGraph-Context") or "").strip().lower()=="ai":
        raise HTTPException(403,"Producer credentials require an explicit dashboard operator")
    payload=await _envelope(request)
    _require_dashboard(request)
    result=await run_in_threadpool(_inspect,operation,workflow_id,revision,payload)
    return JSONResponse(result,headers={"Cache-Control":"no-store"})


@router.post("/v1/process-catalog/procurement-producer/ingest")
async def ingest_monitor_producer(request: Request):
    # Separate write-only capability, never accepted by dashboard or MCP.
    try:
        if request.headers.get("origin"):
            raise HTTPException(403,"Native producer requests only")
        credential=await run_in_threadpool(telemetry.authenticate,request.headers.get("authorization"))
        payload=await _envelope(request,max_bytes=512*1024)
        result=await run_in_threadpool(telemetry.ingest,credential,payload)
        return JSONResponse(result,headers={"Cache-Control":"no-store"})
    except journal.JournalError as exc:
        raise HTTPException(exc.status,str(exc)) from None
    except sqlite3.Error:
        raise HTTPException(503,"Local procurement storage temporarily unavailable") from None
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):
        raise HTTPException(422,"Invalid monitoring producer evidence") from None


@router.get("/v1/process-catalog/procurement-workforce/overview")
async def workforce_overview(request: Request):
    _require_dashboard(request)
    try:
        result=await run_in_threadpool(workforce.overview)
        return JSONResponse(result,headers={"Cache-Control":"no-store"})
    except journal.JournalError as exc:
        raise HTTPException(exc.status,str(exc)) from None
    except (sqlite3.Error,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        raise HTTPException(503,"Local workforce monitoring overview unavailable") from None


@router.post("/v1/process-catalog/procurement-workforce/allocation")
async def workforce_allocation(request: Request):
    _require_dashboard(request)
    payload=await _envelope(request)
    _require_dashboard(request)
    try:
        if not isinstance(payload,dict) or set(payload)!={"document"} or not isinstance(payload["document"],str):
            raise ValueError("exact JSON-text document required")
        result=await run_in_threadpool(lambda:allocation.assess(single.loads(payload["document"])))
        return JSONResponse(result,headers={"Cache-Control":"no-store"})
    except LookupError:
        raise HTTPException(404,"Saved process revision not found") from None
    except sqlite3.Error:
        raise HTTPException(503,"Local procurement storage temporarily unavailable") from None
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):
        raise HTTPException(422,"Invalid, stale, overlapping or oversized workforce allocation evidence") from None


@router.post("/v1/process-catalog/procurement-workforce/exchange/{action}")
async def workforce_exchange(request: Request,action: Literal["export","review","identity","signed-export","signed-review","trust-status","trust-configure","registered-review","inbox-status","inbox-admit","inbox-review","inbox-forget","inbox-retire","inbox-dossier","audit-verify","audit-timeline","audit-triage","audit-reconcile","remediation-create","remediation-list","remediation-status","remediation-change","remediation-forget","remediation-continuity-preview","remediation-continuity-carry","remediation-ticket-status","remediation-ticket-bind","remediation-ticket-check","remediation-jira-preview","remediation-jira-dispatch","remediation-jira-status"]):
    _require_dashboard(request)
    if str(request.headers.get("X-OpenWorkGraph-Context") or "").strip().lower()=="ai":
        raise HTTPException(403,"Exchange requires explicit dashboard operator")
    payload=await _envelope(request)
    _require_dashboard(request)
    try:
        if not isinstance(payload,dict) or set(payload)!={"document"} or not isinstance(payload["document"],str):
            raise ValueError("exact JSON-text document required")
        document=single.loads(payload["document"])
        fn={"export":exchange.export,"review":exchange.review,"identity":signing.identity,
            "signed-export":signing.signed_export,"signed-review":signing.signed_review,
            "trust-status":trust.status,"trust-configure":trust.configure,"registered-review":trust.review,"inbox-status":inbox.status,
            "inbox-admit":inbox.admit,"inbox-review":inbox.review,"inbox-forget":inbox.forget,"inbox-retire":inbox.retire,"inbox-dossier":dossier.freeze_inbox,
            "audit-verify":audit_review.verify,"audit-timeline":audit_review.review_timeline,
            "audit-triage":audit_review.record_triage,
            "audit-reconcile":audit_review.reconcile_receipts,
            "remediation-create":audit_review.create_remediation,"remediation-list":remediation.list_registers,
            "remediation-status":remediation.status,"remediation-change":remediation.change,
            "remediation-forget":remediation.forget,
            "remediation-continuity-preview":audit_review.preview_continuity,
            "remediation-continuity-carry":audit_review.carry_continuity,
            "remediation-ticket-status":remediation_tickets.status,
            "remediation-ticket-bind":remediation_tickets.bind,
            "remediation-ticket-check":remediation_tickets.check,
            "remediation-jira-preview":jira_handoff.preview,
            "remediation-jira-dispatch":jira_handoff.dispatch,
            "remediation-jira-status":jira_handoff.status}[action]
        result=await run_in_threadpool(fn,document)
        return JSONResponse(result,headers={"Cache-Control":"no-store"})
    except journal.JournalError as exc:
        raise HTTPException(exc.status,str(exc)) from None
    except sqlite3.Error:
        raise HTTPException(503,"Local workforce evidence unavailable") from None
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):
        raise HTTPException(422,"Invalid or incompatible workforce exchange evidence") from None
