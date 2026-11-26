"""Routes for sharing a log's process intelligence with connected AI agents (see event_log_agent_sharing).

* ``GET|POST /v1/event-logs/{log_id}/agent-share``: dashboard session only; the owner chooses the level.
* ``GET /v1/process-intelligence/...``: read-only, for the local AI bearer (the MCP server) or the
  dashboard. A bearer additionally needs the global AI-access switch on, and every log must be
  shared at the needed level; otherwise the answer is the same as for an unknown log.
* ``POST /v1/event-logs/{log_id}/agent-share/tags``: dashboard only; lets agents tag cases (cases level).
* ``POST /v1/process-intelligence/{log_id}/case-tags``: the one agent write; adds an ``agent: `` tag.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import event_log_agent_sharing as sharing
from .event_log_routes import _json_body, _require_dashboard
from .event_log_workspace import EventLogWorkspaceError
from .local_auth import dashboard_session_valid

router = APIRouter()
# Set when a request passes _require_agent_access as an agent (not the owner's dashboard), so _answer can
# record the call for the owner (event_log_agent_access).
_AGENT_CALL: ContextVar[bool] = ContextVar("owg_agent_call", default=False)


def _is_dashboard(request: Request) -> bool:
    auth = str(request.headers.get("authorization") or "")
    return auth.lower().startswith("owg-session ") and dashboard_session_valid(auth[len("owg-session "):].strip())


def _require_agent_access(request: Request) -> None:
    if _is_dashboard(request):
        _AGENT_CALL.set(False)
        return
    from .ai_access import ai_access_enabled
    if not ai_access_enabled():
        raise HTTPException(403, "OpenWorkGraph AI access is off")
    _AGENT_CALL.set(True)


async def _answer(operation, *args: Any, **kwargs: Any) -> JSONResponse:
    outcome = "ok"
    try:
        result = await run_in_threadpool(operation, *args, **kwargs)
    except sharing.NotShared as exc:
        outcome = "not_shared"
        raise HTTPException(404, str(exc)) from None
    except sharing.NotAllowed as exc:
        outcome = "not_allowed"
        raise HTTPException(403, str(exc)) from None
    except EventLogWorkspaceError as exc:
        outcome = "invalid"
        raise HTTPException(422, str(exc)) from None
    except Exception:
        outcome = "error"          # recorded as failed; the original error propagates unchanged
        raise
    finally:
        if _AGENT_CALL.get() and args and isinstance(args[0], str):
            from . import event_log_agent_access as agent_access
            await run_in_threadpool(agent_access.record, args[0], getattr(operation, "__name__", "unknown"), outcome)
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


@router.get("/v1/event-logs/{log_id}/agent-access")
async def get_agent_access(request: Request, log_id: str, limit: int = Query(default=50, ge=1, le=500)):
    """Dashboard only: the latest calls connected AI agents made to this log's process tools."""
    from . import event_log_agent_access as agent_access
    _require_dashboard(request)
    return await _answer(agent_access.access, log_id, limit)


@router.post("/v1/process-intelligence/{log_id}/case-tags")
async def agent_tag_cases(request: Request, log_id: str):
    _require_agent_access(request)
    body = await _json_body(request, "agent case tags", strict=True, limit=65536)
    return await _answer(sharing.agent_tag_cases, log_id, body)


@router.post("/v1/process-intelligence/stop-sharing")
async def stop_sharing_all(request: Request):
    """Dashboard only: stop sharing every log with agents, with every tag permission."""
    _require_dashboard(request)
    return await _answer(sharing.stop_sharing_all)


@router.get("/v1/process-intelligence/logs")
async def agent_logs(request: Request):
    _require_agent_access(request)
    return await _answer(sharing.shared_logs)


@router.get("/v1/process-intelligence/{log_id}/trace-clusters")
async def agent_trace_clusters(request: Request, log_id: str, k: int | None = Query(default=None, ge=2, le=12),
                               max_k: int = Query(default=8, ge=2, le=12),
                               case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.trace_clusters, log_id, case_type, k, max_k)


@router.post("/v1/process-intelligence/{log_id}/compliance")
async def agent_compliance(request: Request, log_id: str):
    """POST because the rules are a JSON list. Body: {"rules": [...], "case_type": optional text}."""
    _require_agent_access(request)
    spec = await _json_body(request, "compliance rules", strict=True, limit=65536)
    if not isinstance(spec, dict) or "rules" not in spec or set(spec) - {"rules", "case_type"}:
        raise HTTPException(422, 'send {"rules": [...]} with optional case_type')
    case_type = spec.get("case_type")
    if case_type is not None and (not isinstance(case_type, str) or not 0 < len(case_type) <= 200):
        raise HTTPException(422, "case_type must be a short text")
    return await _answer(sharing.compliance, log_id, spec["rules"], case_type)


@router.get("/v1/process-intelligence/{log_id}/performance-spectrum")
async def agent_performance_spectrum(request: Request, log_id: str, segment: list[str] = Query(default=[], max_length=12),
                                     top: int = Query(default=6, ge=1, le=12), bins: int = Query(default=30, ge=2, le=100),
                                     case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.performance_spectrum, log_id, case_type, segment, top, bins)


@router.get("/v1/process-intelligence/{log_id}/overview")
async def agent_overview(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                         sla_hours: float | None = Query(default=None, gt=0, le=100000)):
    _require_agent_access(request)
    return await _answer(sharing.overview, log_id, case_type, sla_hours)


@router.post("/v1/process-intelligence/{log_id}/overview-for-cases")
async def agent_overview_for_cases(request: Request, log_id: str):
    """POST because the case filter is a JSON object (the same keys as a cohort).

    Body: {"case_filter": {...}, "case_type": optional text, "sla_hours": optional number}."""
    _require_agent_access(request)
    spec = await _json_body(request, "case filter", strict=True, limit=16384)
    if not isinstance(spec, dict) or "case_filter" not in spec or set(spec) - {"case_filter", "case_type", "sla_hours"}:
        raise HTTPException(422, 'send {"case_filter": {...}} with optional case_type and sla_hours')
    case_type, sla = spec.get("case_type"), spec.get("sla_hours")
    if case_type is not None and (not isinstance(case_type, str) or not 0 < len(case_type) <= 200):
        raise HTTPException(422, "case_type must be a short text")
    if sla is not None and (isinstance(sla, bool) or not isinstance(sla, (int, float)) or not 0 < sla <= 100000):
        raise HTTPException(422, "sla_hours must be a number of hours above 0")
    return await _answer(sharing.overview_for_cases, log_id, spec["case_filter"], case_type, sla)


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


@router.post("/v1/process-intelligence/{log_id}/simulate")
async def agent_simulate(request: Request, log_id: str):
    """POST because a scenario is a JSON object. Body: {"scenario": {...}, "case_type": optional text}."""
    _require_agent_access(request)
    spec = await _json_body(request, "scenario", strict=True, limit=65536)
    if not isinstance(spec, dict) or "scenario" not in spec or set(spec) - {"scenario", "case_type"}:
        raise HTTPException(422, 'send {"scenario": {...}} with optional case_type')
    case_type = spec.get("case_type")
    if case_type is not None and (not isinstance(case_type, str) or not 0 < len(case_type) <= 200):
        raise HTTPException(422, "case_type must be a short text")
    return await _answer(sharing.simulate, log_id, spec["scenario"], case_type)


@router.get("/v1/process-intelligence/{log_id}/automation-candidates")
async def agent_automation_candidates(request: Request, log_id: str, limit: int = Query(default=10, ge=1, le=50),
                                      case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.automation_candidates, log_id, case_type, limit)


@router.post("/v1/process-intelligence/{log_id}/costs")
async def agent_costs(request: Request, log_id: str):
    """POST because prices are a JSON object. Body: {"model"?: {...}, "case_type"?: text, "limit"?: 1-100}.
    Without model, the owner's saved cost model is used."""
    _require_agent_access(request)
    spec = await _json_body(request, "cost request", strict=True, limit=131072)
    if not isinstance(spec, dict) or set(spec) - {"model", "case_type", "limit"}:
        raise HTTPException(422, 'send {"model"?: {...}, "case_type"?: ..., "limit"?: ...}')
    case_type = spec.get("case_type")
    if case_type is not None and (not isinstance(case_type, str) or not 0 < len(case_type) <= 200):
        raise HTTPException(422, "case_type must be a short text")
    return await _answer(sharing.costs, log_id, spec.get("model"), case_type, spec.get("limit", 20))


@router.get("/v1/process-intelligence/{log_id}/decision-rules")
async def agent_decision_rules(request: Request, log_id: str, activity: str | None = Query(default=None, max_length=200),
                               case_type: str | None = Query(default=None, max_length=200),
                               max_depth: int = Query(default=3, ge=1, le=4)):
    _require_agent_access(request)
    return await _answer(sharing.decision_rules, log_id, activity, case_type, max_depth)


@router.get("/v1/process-intelligence/{log_id}/open-case-predictions")
async def agent_open_case_predictions(request: Request, log_id: str,
                                      end_activity: list[str] = Query(min_length=1, max_length=20),
                                      outcome: str | None = Query(default=None, pattern="^(duration_over|contains_activity)$"),
                                      outcome_hours: float | None = Query(default=None, gt=0, le=100000),
                                      outcome_activity: str | None = Query(default=None, min_length=1, max_length=200),
                                      limit: int = Query(default=25, ge=1, le=100),
                                      case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.open_case_predictions, log_id, end_activity, case_type, outcome, outcome_hours,
                         outcome_activity, limit)


@router.get("/v1/process-intelligence/{log_id}/open-case-next-activity")
async def agent_open_case_next_activity(request: Request, log_id: str,
                                        end_activity: list[str] = Query(min_length=1, max_length=20),
                                        history: int = Query(default=2, ge=1, le=4),
                                        limit: int = Query(default=25, ge=1, le=100),
                                        case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.open_case_next_activity, log_id, end_activity, case_type, history, limit)


@router.post("/v1/process-intelligence/{log_id}/impact")
async def agent_impact(request: Request, log_id: str):
    """POST because the cohorts are JSON objects. Body: {"intervention_at", "metric"?, "activity"?, "sla_hours"?,
    "window_days"?, "exclude_recent_days"?, "exclude_spanning"?, "treated"?, "control"?, "case_type"?}."""
    _require_agent_access(request)
    spec = await _json_body(request, "impact settings", strict=True, limit=32768)
    if not isinstance(spec, dict):
        raise HTTPException(422, "send the impact settings as an object")
    case_type = spec.pop("case_type", None)
    if case_type is not None and (not isinstance(case_type, str) or not 0 < len(case_type) <= 200):
        raise HTTPException(422, "case_type must be a short text")
    return await _answer(sharing.impact, log_id, spec, case_type)


@router.get("/v1/process-intelligence/{log_id}/temporal-profile")
async def agent_temporal_profile(request: Request, log_id: str, zeta: float = Query(default=3.0, ge=0.5, le=10),
                                 case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.temporal_profile, log_id, case_type, zeta)


@router.get("/v1/process-intelligence/{log_id}/object-map")
async def agent_object_map(request: Request, log_id: str):
    _require_agent_access(request)
    return await _answer(sharing.object_map, log_id)


@router.get("/v1/process-intelligence/{log_id}/heuristics-net")
async def agent_heuristics_net(request: Request, log_id: str,
                               dependency_threshold: float = Query(default=0.5, ge=0, le=1),
                               case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.heuristics_net, log_id, case_type, dependency_threshold)


@router.get("/v1/process-intelligence/{log_id}/data-quality")
async def agent_data_quality(request: Request, log_id: str):
    _require_agent_access(request)
    return await _answer(sharing.data_quality, log_id)


@router.get("/v1/process-intelligence/{log_id}/footprints")
async def agent_footprints(request: Request, log_id: str, noise: float = Query(default=0.2, ge=0, lt=1),
                           case_type: str | None = Query(default=None, max_length=200),
                           case_filter: str | None = Query(default=None, max_length=8192)):
    _require_agent_access(request)
    chosen = None
    if case_filter is not None:
        import json
        try:
            chosen = json.loads(case_filter)
        except ValueError:
            raise HTTPException(422, "case_filter must be a JSON object") from None
        if not isinstance(chosen, dict):
            raise HTTPException(422, "case_filter must be a JSON object")
    return await _answer(sharing.footprints, log_id, case_type, noise, chosen)


@router.get("/v1/process-intelligence/{log_id}/model-quality")
async def agent_model_quality(request: Request, log_id: str, levels: str | None = Query(default=None, max_length=100),
                              case_type: str | None = Query(default=None, max_length=200),
                              case_filter: str | None = Query(default=None, max_length=8192)):
    _require_agent_access(request)
    chosen = None
    if case_filter is not None:
        import json
        try:
            chosen = json.loads(case_filter)
        except ValueError:
            raise HTTPException(422, "case_filter must be a JSON object") from None
        if not isinstance(chosen, dict):
            raise HTTPException(422, "case_filter must be a JSON object")
    return await _answer(sharing.model_quality, log_id, case_type, levels, chosen)


@router.get("/v1/process-intelligence/{log_id}/attributes")
async def agent_attributes(request: Request, log_id: str, top_values: int = Query(default=10, ge=1, le=50),
                           case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.attributes, log_id, case_type, top_values)


@router.post("/v1/process-intelligence/{log_id}/compare-logs")
async def agent_compare_logs(request: Request, log_id: str):
    """Compare this shared log (baseline) with another shared log (comparison), aggregates only."""
    _require_agent_access(request)
    body = await _json_body(request, "log comparison settings", strict=True, limit=16384)
    allowed = {"comparison_log_id", "baseline_case_type", "comparison_case_type", "baseline_filter",
               "comparison_filter", "limit"}
    if not isinstance(body, dict) or set(body) - allowed or not isinstance(body.get("comparison_log_id"), str):
        raise HTTPException(422, "send comparison_log_id and optional case types, filters and limit")
    return await _answer(sharing.compare_logs, log_id, body["comparison_log_id"],
                         baseline_case_type=body.get("baseline_case_type"),
                         comparison_case_type=body.get("comparison_case_type"),
                         baseline_filter=body.get("baseline_filter"), comparison_filter=body.get("comparison_filter"),
                         limit=body.get("limit", 25))
@router.get("/v1/process-intelligence/{log_id}/process-model")
async def agent_process_model(request: Request, log_id: str, noise: float = Query(default=0.2, ge=0, lt=1),
                              activities: float = Query(default=1.0, gt=0, le=1),
                              case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.process_model, log_id, case_type, noise, activities)


@router.get("/v1/process-intelligence/{log_id}/model-deviations")
async def agent_model_deviations(request: Request, log_id: str, noise: float = Query(default=0.2, ge=0, lt=1),
                                 case_type: str | None = Query(default=None, max_length=200),
                                 case_filter: str | None = Query(default=None, max_length=8192)):
    _require_agent_access(request)
    chosen = None
    if case_filter is not None:
        import json
        try:
            chosen = json.loads(case_filter)
        except ValueError:
            raise HTTPException(422, "case_filter must be a JSON object") from None
        if not isinstance(chosen, dict):
            raise HTTPException(422, "case_filter must be a JSON object")
    return await _answer(sharing.model_deviations, log_id, case_type, noise, chosen)


@router.get("/v1/process-intelligence/{log_id}/reference-nets")
async def agent_reference_nets(request: Request, log_id: str):
    _require_agent_access(request)
    return await _answer(sharing.reference_nets, log_id)


@router.get("/v1/process-intelligence/{log_id}/reference-nets/{net_id}/deviations")
async def agent_reference_deviations(request: Request, log_id: str, net_id: str,
                                     case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.reference_deviations, log_id, net_id, case_type)


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


@router.get("/v1/process-intelligence/{log_id}/activity-profile")
async def agent_activity_profile(request: Request, log_id: str,
                                 profile_activity: str = Query(min_length=1, max_length=500),
                                 case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.activity_profile, log_id, profile_activity, case_type)


@router.get("/v1/process-intelligence/{log_id}/signal-scorecard")
async def agent_signal_scorecard(request: Request, log_id: str, months: int = Query(default=6, ge=1, le=24)):
    _require_agent_access(request)
    return await _answer(sharing.signal_scorecard, log_id, months)


@router.get("/v1/process-intelligence/{log_id}/variants")
async def agent_variants(request: Request, log_id: str, limit: int = Query(default=20, ge=1, le=100),
                         case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.variants, log_id, case_type, limit)


@router.get("/v1/process-intelligence/{log_id}/process-map")
async def agent_process_map(request: Request, log_id: str, activities: float = Query(default=1.0, gt=0, le=1),
                            paths: float = Query(default=1.0, gt=0, le=1),
                            case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.process_map, log_id, case_type, activities, paths)


@router.get("/v1/process-intelligence/{log_id}/duration-distribution")
async def agent_duration_distribution(request: Request, log_id: str,
                                      cutoffs_seconds: str = Query(default="3600,14400,86400,259200", max_length=110),
                                      case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    parts = cutoffs_seconds.split(",")
    if not 1 <= len(parts) <= 10 or any(not p.isascii() or not p.isdecimal() or len(p) > 10 for p in parts):
        raise HTTPException(422, "duration thresholds must be comma-separated whole seconds")
    return await _answer(sharing.duration_distribution, log_id, [int(p) for p in parts], case_type)


@router.get("/v1/process-intelligence/{log_id}/time-patterns")
async def agent_time_patterns(request: Request, log_id: str, time_zone: str = Query(default="UTC", min_length=1, max_length=64),
                              activity: str | None = Query(default=None, min_length=1, max_length=200),
                              case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.time_patterns, log_id, case_type, time_zone, activity)


@router.get("/v1/process-intelligence/{log_id}/breakdown")
async def agent_breakdown(request: Request, log_id: str, rows: str = Query(min_length=1, max_length=220),
                          columns: str | None = Query(default=None, max_length=220),
                          with_activity: str | None = Query(default=None, min_length=1, max_length=500),
                          case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.breakdown, log_id, rows, columns, case_type, with_activity)


@router.get("/v1/process-intelligence/{log_id}/similar-cases")
async def agent_similar_cases(request: Request, log_id: str, case_id: str = Query(min_length=1, max_length=500),
                              limit: int = Query(default=10, ge=1, le=50),
                              case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.similar_cases, log_id, case_id, case_type, limit)


@router.get("/v1/process-intelligence/{log_id}/activity-glossary")
async def agent_activity_glossary(request: Request, log_id: str):
    _require_agent_access(request)
    return await _answer(sharing.activity_glossary, log_id)


@router.get("/v1/process-intelligence/{log_id}/signals")
async def agent_signals(request: Request, log_id: str):
    _require_agent_access(request)
    return await _answer(sharing.signals, log_id)


@router.get("/v1/process-intelligence/{log_id}/rework")
async def agent_rework(request: Request, log_id: str, limit: int = Query(default=20, ge=1, le=100),
                       case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.rework, log_id, case_type, limit)


@router.get("/v1/process-intelligence/{log_id}/waiting-times")
async def agent_waiting_times(request: Request, log_id: str, case_type: str | None = Query(default=None, max_length=200),
                              working_calendar: str | None = Query(default=None, max_length=4096)):
    _require_agent_access(request)
    calendar = None
    if working_calendar is not None:
        import json
        try:
            calendar = json.loads(working_calendar)
        except ValueError:
            raise HTTPException(422, "working_calendar must be a JSON object") from None
        if not isinstance(calendar, dict):
            raise HTTPException(422, "working_calendar must be a JSON object")
    return await _answer(sharing.waiting_times, log_id, case_type, calendar)


@router.get("/v1/process-intelligence/{log_id}/drift")
async def agent_drift(request: Request, log_id: str, window: str = Query(default="month", pattern="^(week|month)$"),
                      case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.drift, log_id, case_type, window)


@router.get("/v1/process-intelligence/{log_id}/stuck-cases")
async def agent_stuck_cases(request: Request, log_id: str, limit: int = Query(default=25, ge=1, le=100),
                            case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.stuck_cases, log_id, case_type, limit)


@router.get("/v1/process-intelligence/{log_id}/slowest-cases")
async def agent_slowest(request: Request, log_id: str, limit: int = Query(default=10, ge=1, le=50),
                        case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.slowest_cases, log_id, case_type, limit)


@router.get("/v1/process-intelligence/{log_id}/co-occurrence")
async def agent_cooccurrence(request: Request, log_id: str, limit: int = Query(default=12, ge=2, le=25),
                             min_support: int = Query(default=5, ge=5, le=1_000_000),
                             case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.cooccurrence, log_id, case_type, limit, min_support)


@router.get("/v1/process-intelligence/{log_id}/work-in-progress")
async def agent_work_in_progress(request: Request, log_id: str, bucket: str = Query(default="week", pattern="^(day|week)$"),
                                 end_activity: list[str] = Query(default=[], max_length=20),
                                 case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.work_in_progress, log_id, case_type, bucket, end_activity)


@router.get("/v1/process-intelligence/{log_id}/batching")
async def agent_batching(request: Request, log_id: str, gap_seconds: int = Query(default=60, ge=0, le=86_400),
                         min_batch_size: int = Query(default=3, ge=2, le=1000), by_resource: bool = True,
                         case_type: str | None = Query(default=None, max_length=200)):
    _require_agent_access(request)
    return await _answer(sharing.batching, log_id, case_type, gap_seconds, min_batch_size, by_resource)
