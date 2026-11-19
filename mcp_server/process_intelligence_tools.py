from __future__ import annotations

"""Read-only process-intelligence MCP tools over event logs the owner shared with agents.

Listed only once the owner shares at least one saved event log from the dashboard; each
call still needs the AI-access switch, and each log answers only at the level it was shared.
"""
import re
from typing import Any

from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError

_LOG_ID = re.compile(r"^log_[0-9a-f]{16}$")
_OUTCOMES = {"duration_over", "rework", "contains_activity", "ends_with"}
_REGISTERED: set[int] = set()
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_NOTE = ("Describes recorded events in an owner-shared event log. Durations are elapsed time between recorded "
         "events; associations are not causes. Nothing here authorizes an action or changes the log.")


def register_process_intelligence_tools(mcp: Any, runtime_module: Any) -> None:
    sid = id(mcp)
    if sid in _REGISTERED:
        return
    _REGISTERED.add(sid)

    def _get(tool: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime_module.authorize_tool(tool)
        try:
            result = runtime_module.secure_get(path, {k: v for k, v in (params or {}).items() if v is not None})
        except Exception as exc:  # the server's message (not shared, invalid input) is the useful answer
            raise ToolError(str(exc) or "process intelligence request failed") from None
        result["interpretation"] = _NOTE
        return runtime_module.core._finish(tool, result)

    def _log(log_id: str) -> str:
        if not _LOG_ID.fullmatch(str(log_id or "")):
            raise ToolError("Use a log_id returned by list_process_logs.")
        return log_id

    @mcp.tool(annotations=_READ_ONLY)
    def list_process_logs() -> dict[str, Any]:
        """List the saved process event logs the owner shared with agents.

        Each entry has a log_id, name, sharing level (aggregates or cases), size and its case
        notions (object types). Use a log_id from here with the other process tools. Logs the
        owner did not share are never listed.
        """
        return _get("list_process_logs", "/v1/process-intelligence/logs")

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_overview(log_id: str, case_type: str | None = None, sla_hours: float | None = None) -> dict[str, Any]:
        """Overview of a shared process: case counts, durations, KPIs, top variants and automatic insights.

        Use when asked how a process performs, where time goes or what to look at first.
        case_type picks the case notion (object type) for object-centric logs; sla_hours adds
        SLA breach rates. Insights include a case filter (cohort) describing their cases.
        """
        return _get("get_process_overview", f"/v1/process-intelligence/{_log(log_id)}/overview",
                    {"case_type": case_type, "sla_hours": sla_hours})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_root_cause(log_id: str, outcome: str, hours: float | None = None,
                               activity: str | None = None, case_type: str | None = None) -> dict[str, Any]:
        """Which activities, resources and case attributes are associated with an outcome.

        outcome is duration_over (needs hours), rework, contains_activity or ends_with (both need
        activity). Returns risk differences with confidence intervals and a Bonferroni flag.
        These are associations in the log, never causes.
        """
        if outcome not in _OUTCOMES:
            raise ToolError("outcome must be duration_over, rework, contains_activity or ends_with")
        return _get("get_process_root_cause", f"/v1/process-intelligence/{_log(log_id)}/root-cause",
                    {"outcome": outcome, "hours": hours, "activity": activity, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def explain_process_case(log_id: str, case_id: str, case_type: str | None = None) -> dict[str, Any]:
        """Explain one case: its steps, which gaps took longer than usual for that transition,
        repeated and uncommon steps, and how its duration compares with the log.

        Needs a log shared at the cases level. Use a case_id from get_slowest_process_cases or
        one the user named; never guess identifiers.
        """
        if not isinstance(case_id, str) or not 0 < len(case_id) <= 500:
            raise ToolError("case_id must be a non-empty string of at most 500 characters")
        return _get("explain_process_case", f"/v1/process-intelligence/{_log(log_id)}/case",
                    {"case_id": case_id, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_automation_candidates(log_id: str, limit: int = 10, case_type: str | None = None) -> dict[str, Any]:
        """Activities of a shared log ranked as automation candidates, with every score component.

        Score = 100 x (0.4 volume + 0.4 routine routing + 0.2 share of waiting before the activity).
        Each activity also has its routing pattern (fixed, mostly_fixed, variable), median wait before it,
        resources and repeats per case. Evidence for a review, not a recommendation: waiting is the gap since
        the previous event, no savings are estimated, and nothing authorizes automating anything.
        """
        return _get("get_process_automation_candidates", f"/v1/process-intelligence/{_log(log_id)}/automation-candidates",
                    {"limit": max(1, min(int(limit), 50)), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_decision_rules(log_id: str, activity: str | None = None, max_depth: int = 3,
                                   case_type: str | None = None) -> dict[str, Any]:
        """Decision mining on a shared log. Without an activity: the decision points (activities after which
        cases go different ways) with each branch's share. With an activity: a small decision tree learned
        from case attributes and event attributes recorded at or before that activity, with held-out
        accuracy against always predicting the most common branch. Rules are associations in this log,
        not the business rule or its cause; a rule that does not beat the baseline is noise.
        """
        if activity is not None and (not isinstance(activity, str) or not 0 < len(activity) <= 200):
            raise ToolError("activity must be a non-empty string of at most 200 characters")
        return _get("get_process_decision_rules", f"/v1/process-intelligence/{_log(log_id)}/decision-rules",
                    {"activity": activity, "max_depth": max(1, min(int(max_depth), 4)), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_temporal_profile(log_id: str, zeta: float = 3.0, case_type: str | None = None) -> dict[str, Any]:
        """Temporal profile of a shared log: for each ordered pair of activities in a case, the mean and
        standard deviation of the time between them, and how many occurrences lie more than zeta standard
        deviations away (slower or faster). Aggregates only, no case IDs. Unusual timings to review, not errors.
        """
        if isinstance(zeta, bool) or not isinstance(zeta, (int, float)) or not 0.5 <= zeta <= 10:
            raise ToolError("zeta must be a number from 0.5 to 10")
        return _get("get_process_temporal_profile", f"/v1/process-intelligence/{_log(log_id)}/temporal-profile",
                    {"zeta": float(zeta), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_heuristics_net(log_id: str, dependency_threshold: float = 0.5,
                                   case_type: str | None = None) -> dict[str, Any]:
        """Heuristics-miner model of a shared log: arrows kept only where one activity follows another clearly
        more often than the reverse (dependency at or above the threshold), with each split and join marked AND
        (branches occur in both orders) or XOR (one or the other), and self- and two-step loops. Inferred from
        recorded order, not a modelled process.
        """
        if isinstance(dependency_threshold, bool) or not isinstance(dependency_threshold, (int, float)) \
                or not 0 <= dependency_threshold <= 1:
            raise ToolError("dependency_threshold must be a number from 0 to 1")
        return _get("get_process_heuristics_net", f"/v1/process-intelligence/{_log(log_id)}/heuristics-net",
                    {"dependency_threshold": float(dependency_threshold), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_footprints(log_id: str, noise: float = 0.2, case_type: str | None = None) -> dict[str, Any]:
        """Footprint comparison of a shared log: for every pair of activities, whether one directly follows the
        other (→), both orders occur (||) or neither (#), once from the recorded cases and once from the discovered
        model, with the orderings only the log has (and how many cases took them) or only the model allows.
        Ordering only, not frequency or timing. Aggregates only, no case IDs.
        """
        if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
            raise ToolError("noise must be a share from 0 up to (not including) 1")
        return _get("get_process_footprints", f"/v1/process-intelligence/{_log(log_id)}/footprints",
                    {"noise": float(noise), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_model_quality(log_id: str, noise_levels: list[float] | None = None,
                                  case_type: str | None = None) -> dict[str, Any]:
        """How good the discovered process model of a shared log is at each noise level: fitness (can it replay
        the cases), precision (does it allow little else), their F-score, generalization and simplicity, plus the
        noise level with the best F-score. Use it to pick a noise level before asking for the model or its
        footprints. Scores describe drafts against this log, not which model is right. Aggregates only.
        """
        params: dict[str, Any] = {"case_type": case_type}
        if noise_levels is not None:
            if (not isinstance(noise_levels, list) or not 1 <= len(noise_levels) <= 8
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < 1 for v in noise_levels)):
                raise ToolError("noise_levels must be 1 to 8 shares from 0 up to (not including) 1")
            params["levels"] = ",".join(str(float(v)) for v in noise_levels)
        return _get("get_process_model_quality", f"/v1/process-intelligence/{_log(log_id)}/model-quality", params)

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_deviations(log_id: str, noise: float = 0.2, case_type: str | None = None) -> dict[str, Any]:
        """Exactly where the recorded cases of a shared log depart from its discovered process model, from optimal
        alignments: the steps cases took that the model does not allow there, the steps the model requires that
        cases skipped (each with case counts), alignment fitness, and how each non-fitting variant is explained
        step by step. Pick the noise level with get_process_model_quality first. A deviation is a difference from a
        draft model, not a verified error. Aggregates only, no case IDs.
        """
        if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
            raise ToolError("noise must be a share from 0 up to (not including) 1")
        return _get("get_process_deviations", f"/v1/process-intelligence/{_log(log_id)}/model-deviations",
                    {"noise": float(noise), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_signals(log_id: str) -> dict[str, Any]:
        """The owner's signal rules on a shared log (KPI thresholds, case and compliance conditions) and their
        last evaluated state: triggered or not, the measured value or matching cases, and whether the owner
        reviewed it. "stale" means the log changed since the last evaluation.

        Read-only: this never evaluates, acknowledges or snoozes a signal. Case IDs are included only for logs
        shared at the cases level; follow up with explain_process_case. A triggered signal is not a verified
        problem or an instruction to act.
        """
        return _get("get_process_signals", f"/v1/process-intelligence/{_log(log_id)}/signals", {})

    @mcp.tool(annotations=_READ_ONLY)
    def get_slowest_process_cases(log_id: str, limit: int = 10, case_type: str | None = None) -> dict[str, Any]:
        """The slowest cases of a shared log (first to last recorded event), slowest first.

        Needs a log shared at the cases level. Follow up with explain_process_case.
        """
        return _get("get_slowest_process_cases", f"/v1/process-intelligence/{_log(log_id)}/slowest-cases",
                    {"limit": max(1, min(int(limit), 50)), "case_type": case_type})


    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def tag_process_cases(log_id: str, tag: str, case_ids: list[str], note: str = "") -> dict[str, Any]:
        """Record a decision about cases as a case tag, for example "needs review" or "duplicate invoice".

        Only works if the owner shared the log at the cases level AND separately allowed agents to
        tag cases. The tag is stored as "agent: <tag>" with a note that an AI agent added it, so the
        owner can see and remove it. Use case IDs exactly as returned by the other process tools.
        This changes nothing else and authorizes no action in any business system.
        """
        if not isinstance(tag, str) or not 0 < len(tag.strip()) <= 52:
            raise ToolError("tag must be a non-empty text of at most 52 characters")
        if not isinstance(case_ids, list) or not 0 < len(case_ids) <= 1000 or not all(isinstance(c, str) and c for c in case_ids):
            raise ToolError("case_ids must be a list of 1-1000 case IDs")
        runtime_module.authorize_tool("tag_process_cases")
        try:
            result = runtime_module.secure_post(f"/v1/process-intelligence/{_log(log_id)}/case-tags",
                                                {"tag": tag, "case_ids": case_ids, "note": str(note or "")[:400]})
        except Exception as exc:
            raise ToolError(str(exc) or "tagging failed") from None
        return runtime_module.core._finish("tag_process_cases", result)


__all__ = ["register_process_intelligence_tools"]
