"""Routes for sharing a log's process intelligence with connected AI agents (see event_log_agent_sharing).

* ``GET|POST /v1/event-logs/{log_id}/agent-share``: dashboard session only; the owner chooses the level.
* ``GET /v1/process-intelligence/...``: read-only, for the local AI bearer (the MCP server) or the
  dashboard. A bearer additionally needs the global AI-access switch on, and every log must be
  shared at the needed level; otherwise the answer is the same as for an unknown log.
* ``POST /v1/event-logs/{log_id}/agent-share/tags``: dashboard only; lets agents tag cases (cases level).
* ``POST /v1/process-intelligence/{log_id}/case-tags``: the one agent write; adds an ``agent: `` tag.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import event_log_agent_sharing as sharing
from .event_log_routes import _json_body, _require_dashboard
from .event_log_workspace import EventLogWorkspaceError
from .local_auth import dashboard_session_valid

router = APIRouter()


def _is_dashboard(request: Request) -> bool:
    auth = str(request.headers.get("authorization") or "")
    return auth.lower().startswith("owg-session ") and dashboard_session_valid(auth[len("owg-session "):].strip())


def _require_agent_access(request: Request) -> None:
    if _is_dashboard(request):
        return
    from .ai_access import ai_access_enabled
    if not ai_access_enabled():
        raise HTTPException(403, "OpenWorkGraph AI access is off")


async def _answer(operation, *args: Any, **kwargs: Any) -> JSONResponse:
    try:
        result = await run_in_threadpool(operation, *args, **kwargs)
    except sharing.NotShared as exc:
        raise HTTPException(404, str(exc)) from None
    except sharing.NotAllowed as exc:
        raise HTTPException(403, str(exc)) from None
    except EventLogWorkspaceError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/v1/event-logs/{log_id}/agent-share")
async def get_agent_share(request: Request, log_id: str):
    _require_dashboard(request)
    return await _answer(sharing.share_status, log_id)


@router.post("/v1/event-logs/{log_id}/agent-share")
async def set_agent_share(request: Request, log_id: str):
    _require_dashboard(request)
    body = await _json_body(request, "agent sharing", strict=True, limit=1024)
    if not isinstance(body, dict) or set(body) != {"level"}:
        raise HTTPException(422, 'send {"level": "aggregates" | "cases" | null}')
    return await _answer(sharing.set_share, log_id, body["level"])


@router.post("/v1/event-logs/{log_id}/agent-share/tags")
async def set_agent_tag_grant(request: Request, log_id: str):
    _require_dashboard(request)
    body = await _json_body(request, "agent tag permission", strict=True, limit=1024)
    if not isinstance(body, dict) or set(body) != {"allowed"}:
        raise HTTPException(422, 'send {"allowed": true | false}')
    return await _answer(sharing.set_tag_grant, log_id, body["allowed"])


@router.post("/v1/process-intelligence/{log_id}/case-tags")
async def agent_tag_cases(request: Request, log_id: str):
    _require_agent_access(request)
    body = await _json_body(request, "agent case tags", strict=True, limit=65536)
    return await _answer(sharing.agent_tag_cases, log_id, body)


@router.get("/v1/process-intelligence/logs")
async def agent_logs(request: Request):
    _require_agent_access(request)
    return await _answer(sharing.shared_logs)


@router.get("/v1/process-intelligence/{log_id}/overview")
async def agent_overview(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                         sla_hours: float | None = Query(default=None, gt=0, le=100000)):
    _require_agent_access(request)
    return await _answer(sharing.overview, log_id, case_type, sla_hours)


@router.get("/v1/process-intelligence/{log_id}/root-cause")
async def agent_root_cause(request: Request, log_id: str,
                           outcome: str = Query(pattern="^(duration_over|rework|contains_activity|ends_with)$"),
                           hours: float | None = Query(default=None, gt=0, le=100000),
                           activity: str | None = Query(default=None, max_length=200),
                           case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.root_cause, log_id, outcome, case_type, hours, activity)


@router.get("/v1/process-intelligence/{log_id}/case")
async def agent_case(request: Request, log_id: str, case_id: str = Query(min_length=1, max_length=500),
                     case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.explain_case, log_id, case_id, case_type)


@router.get("/v1/process-intelligence/{log_id}/automation-candidates")
async def agent_automation_candidates(request: Request, log_id: str, limit: int = Query(default=10, ge=1, le=50),
                                      case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.automation_candidates, log_id, case_type, limit)


@router.get("/v1/process-intelligence/{log_id}/decision-rules")
async def agent_decision_rules(request: Request, log_id: str, activity: str | None = Query(default=None, max_length=200),
                               case_type: str | None = Query(default=None, max_length=200),
                               max_depth: int = Query(default=3, ge=1, le=4)):
    _require_agent_access(request)
    return await _answer(sharing.decision_rules, log_id, activity, case_type, max_depth)


@router.get("/v1/process-intelligence/{log_id}/temporal-profile")
async def agent_temporal_profile(request: Request, log_id: str, zeta: float = Query(default=3.0, ge=0.5, le=10),
                                 case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.temporal_profile, log_id, case_type, zeta)


@router.get("/v1/process-intelligence/{log_id}/heuristics-net")
async def agent_heuristics_net(request: Request, log_id: str,
                               dependency_threshold: float = Query(default=0.5, ge=0, le=1),
                               case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.heuristics_net, log_id, case_type, dependency_threshold)


@router.get("/v1/process-intelligence/{log_id}/footprints")
async def agent_footprints(request: Request, log_id: str, noise: float = Query(default=0.2, ge=0, lt=1),
                           case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.footprints, log_id, case_type, noise)


@router.get("/v1/process-intelligence/{log_id}/model-quality")
async def agent_model_quality(request: Request, log_id: str, levels: str | None = Query(default=None, max_length=100),
                              case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.model_quality, log_id, case_type, levels)


@router.get("/v1/process-intelligence/{log_id}/model-deviations")
async def agent_model_deviations(request: Request, log_id: str, noise: float = Query(default=0.2, ge=0, lt=1),
                                 case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.model_deviations, log_id, case_type, noise)


@router.get("/v1/process-intelligence/{log_id}/funnel")
async def agent_funnel(request: Request, log_id: str, step: list[str] = Query(default=[], max_length=500),
                       within_hours: float | None = Query(default=None, gt=0),
                       case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.funnel, log_id, step, case_type, within_hours)


@router.get("/v1/process-intelligence/{log_id}/unusual-cases")
async def agent_unusual_cases(request: Request, log_id: str, limit: int = Query(default=25, ge=1, le=50),
                              case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.unusual_cases, log_id, case_type, limit)


@router.get("/v1/process-intelligence/{log_id}/due-dates")
async def agent_due_dates(request: Request, log_id: str, due_activity: str = Query(min_length=1, max_length=500),
                          due_attribute: str | None = Query(default=None, max_length=200),
                          due_days: float | None = Query(default=None, gt=0),
                          early_days: float | None = Query(default=None, gt=0),
                          case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.due_dates, log_id, due_activity, case_type, due_attribute, due_days, early_days)


@router.get("/v1/process-intelligence/{log_id}/next-steps")
async def agent_next_steps(request: Request, log_id: str, step: list[str] = Query(default=[], max_length=500),
                           anchor: str = Query(default="start", max_length=20),
                           case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.next_steps, log_id, step, case_type, anchor)


@router.get("/v1/process-intelligence/{log_id}/insights")
async def agent_insights(request: Request, log_id: str, sla_hours: float | None = Query(default=None, gt=0),
                         limit: int = Query(default=8, ge=1, le=30),
                         case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.insights, log_id, case_type, sla_hours, limit)


@router.get("/v1/process-intelligence/{log_id}/model-deviation-causes")
async def agent_model_deviation_causes(request: Request, log_id: str, noise: float = Query(default=0.2, ge=0, lt=1),
                                       case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.model_deviation_causes, log_id, case_type, noise)


@router.get("/v1/process-intelligence/{log_id}/case-alignment")
async def agent_case_alignment(request: Request, log_id: str, case_id: str = Query(min_length=1, max_length=500),
                               noise: float = Query(default=0.2, ge=0, lt=1),
                               case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.case_alignment, log_id, case_id, case_type, noise)


@router.get("/v1/process-intelligence/{log_id}/brief")
async def agent_brief(request: Request, log_id: str, sla_hours: float | None = Query(default=None, gt=0),
                      case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.brief, log_id, case_type, sla_hours)


@router.get("/v1/process-intelligence/{log_id}/model-change")
async def agent_model_change(request: Request, log_id: str, split: str | None = Query(default=None, max_length=64),
                             noise: float = Query(default=0.2, ge=0, lt=1),
                             case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.model_change, log_id, case_type, split, noise)


@router.get("/v1/process-intelligence/{log_id}/model-comparison")
async def agent_model_comparison(request: Request, log_id: str, group_a: str = Query(min_length=3, max_length=400),
                                 group_b: str = Query(min_length=3, max_length=400),
                                 noise: float = Query(default=0.2, ge=0, lt=1),
                                 case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.model_comparison, log_id, group_a, group_b, case_type, noise)


@router.get("/v1/process-intelligence/{log_id}/case-compare")
async def agent_case_compare(request: Request, log_id: str, case_a: str = Query(min_length=1, max_length=500),
                             case_b: str = Query(min_length=1, max_length=500),
                             case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.compare_cases, log_id, case_a, case_b, case_type)


@router.get("/v1/process-intelligence/{log_id}/signals")
async def agent_signals(request: Request, log_id: str):
    _require_agent_access(request)
    return await _answer(sharing.signals, log_id)


@router.get("/v1/process-intelligence/{log_id}/slowest-cases")
async def agent_slowest(request: Request, log_id: str, limit: int = Query(default=10, ge=1, le=50),
                        case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.slowest_cases, log_id, case_type, limit)
