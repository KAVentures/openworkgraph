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
    def get_process_brief(log_id: str, sla_hours: float | None = None, case_type: str | None = None) -> dict[str, Any]:
        """Start here: one compact briefing on a shared log. Size, timing and KPIs, the most common paths, the top
        automatic insights, how well the discovered model fits at its best noise level, the steps cases most often
        add or skip compared with it, how many cases stand out and why, and which tools to call next. Parts that
        cannot be computed are listed in parts_unavailable. Aggregates only; describes records, not causes.
        """
        if sla_hours is not None and (isinstance(sla_hours, bool) or not isinstance(sla_hours, (int, float))
                                      or sla_hours <= 0):
            raise ToolError("sla_hours must be a positive number")
        return _get("get_process_brief", f"/v1/process-intelligence/{_log(log_id)}/brief",
                    {"sla_hours": sla_hours, "case_type": case_type})

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
    def list_process_reference_nets(log_id: str) -> dict[str, Any]:
        """The reference process models (Petri nets) the owner saved for a shared log, by id and name, such as the
        approved or designed process. Use the net_id with get_process_reference_deviations. Aggregates only.
        """
        return _get("list_process_reference_nets", f"/v1/process-intelligence/{_log(log_id)}/reference-nets", {})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_reference_deviations(log_id: str, net_id: str, case_type: str | None = None) -> dict[str, Any]:
        """Where the recorded cases of a shared log depart from one of the owner's saved reference models, from
        optimal alignments: steps cases took that the model does not allow there, steps it requires that cases
        skipped (with case counts), alignment fitness overall and by month, and how each non-fitting variant is
        explained. A deviation is a difference from the saved model, not a verified error. Aggregates only.
        """
        if not isinstance(net_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", net_id):
            raise ToolError("net_id must be a reference net id from list_process_reference_nets")
        return _get("get_process_reference_deviations",
                    f"/v1/process-intelligence/{_log(log_id)}/reference-nets/{net_id}/deviations",
                    {"case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_funnel(log_id: str, steps: list[str], within_hours: float | None = None,
                           case_type: str | None = None) -> dict[str, Any]:
        """Milestone funnel of a shared log: for 2 to 8 activities in order, how many cases reached each step
        after the previous ones, the conversion from the previous step, the hours between steps (median, p90),
        and what cases that stopped did last. within_hours optionally counts a step only within that many hours
        of the previous one. Shows how far cases got, not why. Aggregates only, no case IDs.
        """
        if not isinstance(steps, list) or not 2 <= len(steps) <= 8 or any(not isinstance(s, str) or not s for s in steps):
            raise ToolError("steps must be 2 to 8 activity names")
        if within_hours is not None and (isinstance(within_hours, bool) or not isinstance(within_hours, (int, float))
                                         or within_hours <= 0):
            raise ToolError("within_hours must be a positive number")
        params: dict[str, Any] = {"step": steps, "case_type": case_type}
        if within_hours is not None:
            params["within_hours"] = float(within_hours)
        return _get("get_process_funnel", f"/v1/process-intelligence/{_log(log_id)}/funnel", params)

    @mcp.tool(annotations=_READ_ONLY)
    def get_unusual_process_cases(log_id: str, limit: int = 25, case_type: str | None = None) -> dict[str, Any]:
        """Cases of a shared log that stand out from the rest, each with its reasons: a rare path, unusually slow
        or fast, unusually many events, heavy rework, or an activity almost no other case has, measured against
        the log (robust z-scores and frequencies). Case IDs and paths appear only for logs shared at the cases
        level; follow up with explain_process_case. Unusual is not wrong: this ranks cases for a look.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ToolError("limit must be 1 to 50")
        return _get("get_unusual_process_cases", f"/v1/process-intelligence/{_log(log_id)}/unusual-cases",
                    {"limit": limit, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_due_dates(log_id: str, activity: str, due_attribute: str | None = None,
                              due_days: float | None = None, early_days: float | None = None,
                              case_type: str | None = None) -> dict[str, Any]:
        """Due-date adherence in a shared log: whether `activity` (e.g. a payment or shipment) happened by each
        case's due date, taken from a case or event attribute (`due_attribute`) or a fixed number of days after
        the case started (`due_days`); give exactly one. Returns on-time, early (with `early_days`), late and
        still-open (overdue or not yet due) counts, days late, and a trend by due month. Case IDs of the latest
        late cases only for logs shared at the cases level. Says how often deadlines were met, not why.
        """
        if not isinstance(activity, str) or not activity:
            raise ToolError("activity must name an activity")
        if (due_attribute is None) == (due_days is None):
            raise ToolError("give either due_attribute or due_days")
        for name, value in (("due_days", due_days), ("early_days", early_days)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
                raise ToolError(f"{name} must be a positive number of days")
        return _get("get_process_due_dates", f"/v1/process-intelligence/{_log(log_id)}/due-dates",
                    {"due_activity": activity, "due_attribute": due_attribute, "due_days": due_days,
                     "early_days": early_days, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_next_steps(log_id: str, steps: list[str] | None = None, anchor: str = "start",
                               case_type: str | None = None) -> dict[str, Any]:
        """Walk a shared log's process one step at a time: after `steps` (empty = the start of the cases), which
        activities come next, in how many cases, the hours from the last step to each (median, p90) and from each
        to the case end, and how many cases end there. anchor="start" means cases that begin with `steps`;
        anchor="anywhere" finds the first place the steps occur one right after another. Call again with a next
        step appended to go deeper. Order and timing only, not why cases branch. Aggregates only.
        """
        steps = steps or []
        if not isinstance(steps, list) or len(steps) > 30 or any(not isinstance(s, str) or not s for s in steps):
            raise ToolError("steps must be up to 30 activity names")
        if anchor not in ("start", "anywhere"):
            raise ToolError("anchor must be start or anywhere")
        return _get("get_process_next_steps", f"/v1/process-intelligence/{_log(log_id)}/next-steps",
                    {"step": steps, "anchor": anchor, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_insights(log_id: str, sla_hours: float | None = None, limit: int = 8,
                             case_type: str | None = None) -> dict[str, Any]:
        """Where to look first in a shared log: ranked automatic findings (slow step-to-step gaps, repeated
        activities, costly detours, the long tail of slow cases, how spread the paths are, whether newer cases
        are slower, and SLA breaches when sla_hours is given), each with its numbers, the share of cases it
        touches and the case filter that reproduces it. A good first call before the other process tools.
        Descriptive pointers, not causes. Aggregates only, no case IDs.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
            raise ToolError("limit must be 1 to 30")
        if sla_hours is not None and (isinstance(sla_hours, bool) or not isinstance(sla_hours, (int, float))
                                      or sla_hours <= 0):
            raise ToolError("sla_hours must be a positive number")
        return _get("get_process_insights", f"/v1/process-intelligence/{_log(log_id)}/insights",
                    {"sla_hours": sla_hours, "limit": limit, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_deviation_causes(log_id: str, noise: float = 0.2, case_type: str | None = None) -> dict[str, Any]:
        """Which case attributes and activities go with cases that depart from a shared log's discovered process
        model: risk differences with 95% intervals and a Bonferroni check, for the outcome "the case followed a
        path the optimal alignment found non-fitting". Use after get_process_deviations. Associations, not
        causes. Aggregates only, no case IDs.
        """
        if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
            raise ToolError("noise must be a share from 0 up to (not including) 1")
        return _get("get_process_deviation_causes", f"/v1/process-intelligence/{_log(log_id)}/model-deviation-causes",
                    {"noise": float(noise), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def align_process_case(log_id: str, case_id: str, noise: float = 0.2, case_type: str | None = None) -> dict[str, Any]:
        """How one case of a shared log departs from its discovered process model: the optimal alignment step by
        step, the steps it did that the model does not allow there and the steps the model requires that it
        skipped, with its alignment fitness. Needs a log shared at the cases level; pair with
        explain_process_case for timing. A deviation is a difference from a draft model, not an error.
        """
        if not isinstance(case_id, str) or not case_id:
            raise ToolError("case_id must be a case ID")
        if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
            raise ToolError("noise must be a share from 0 up to (not including) 1")
        return _get("align_process_case", f"/v1/process-intelligence/{_log(log_id)}/case-alignment",
                    {"case_id": case_id, "noise": float(noise), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_model_change(log_id: str, split: str | None = None, noise: float = 0.2,
                                 case_type: str | None = None) -> dict[str, Any]:
        """How a shared log's discovered process model differs before and after a date (ISO; default: the median
        case start): which activity orderings changed (footprint relations), which activities appear in only one
        period, and how well each period's cases fit the other period's model. Use after concept drift to see what
        changed. Drafts from recorded events, not causes. Aggregates only.
        """
        if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
            raise ToolError("noise must be a share from 0 up to (not including) 1")
        return _get("get_process_model_change", f"/v1/process-intelligence/{_log(log_id)}/model-change",
                    {"split": split, "noise": float(noise), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def compare_process_models(log_id: str, group_a: str, group_b: str, noise: float = 0.2,
                               case_type: str | None = None) -> dict[str, Any]:
        """Compare how two groups of cases in a shared log run, each chosen by a case attribute value written as
        "attribute=value" (for example group_a="region=EU", group_b="region=US"): activity orderings that differ
        between their discovered models, activities only one group has, and how well each group's cases fit the
        other group's model. Recorded behaviour only; groups also differ in case mix. Aggregates only.
        """
        for name, value in (("group_a", group_a), ("group_b", group_b)):
            if not isinstance(value, str) or "=" not in value:
                raise ToolError(f"{name} must look like attribute=value")
        if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
            raise ToolError("noise must be a share from 0 up to (not including) 1")
        return _get("compare_process_models", f"/v1/process-intelligence/{_log(log_id)}/model-comparison",
                    {"group_a": group_a, "group_b": group_b, "noise": float(noise), "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def compare_process_cases(log_id: str, case_a: str, case_b: str, case_type: str | None = None) -> dict[str, Any]:
        """Put two cases of a shared log side by side, e.g. a slow case against a normal one: their steps aligned
        (shared, only in case_a, only in case_b), the hours from each case's start to every step, and between
        which shared steps case_b fell furthest behind. Needs a log shared at the cases level. Describes two
        recorded cases; a difference is not a cause.
        """
        for name, value in (("case_a", case_a), ("case_b", case_b)):
            if not isinstance(value, str) or not value:
                raise ToolError(f"{name} must be a case ID")
        return _get("compare_process_cases", f"/v1/process-intelligence/{_log(log_id)}/case-compare",
                    {"case_a": case_a, "case_b": case_b, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_activity_profile(log_id: str, activity: str, case_type: str | None = None) -> dict[str, Any]:
        """One activity of a shared log from every side: how many cases have it and how often it repeats, what
        usually comes before and after it (with median gaps), how long into a case it first happens, when in the
        week and day it occurs, and case durations with and without it. Associations, not causes. Aggregates only.
        """
        if not isinstance(activity, str) or not activity:
            raise ToolError("activity must name an activity")
        return _get("get_process_activity_profile", f"/v1/process-intelligence/{_log(log_id)}/activity-profile",
                    {"profile_activity": activity, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_signal_scorecard(log_id: str, months: int = 6) -> dict[str, Any]:
        """The owner's value-based signals on a shared log (KPI thresholds, conformance and due-date rules) month by
        month, by when cases started: each month's value and whether the rule's condition held. Read-only; it never
        evaluates or reviews signals. Use it to see whether a problem is new or improving. Aggregates only.
        """
        if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 24:
            raise ToolError("months must be 1 to 24")
        return _get("get_process_signal_scorecard", f"/v1/process-intelligence/{_log(log_id)}/signal-scorecard",
                    {"months": months})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_breakdown(log_id: str, rows: str, columns: str | None = None, with_activity: str | None = None,
                              case_type: str | None = None) -> dict[str, Any]:
        """Case figures of a shared log by one or two dimensions, like a pivot table: cases, share, median and
        90th-percentile duration, events per case, rework rate and, with with_activity, the share of cases doing
        that activity. rows/columns are start_month, start_weekday, start_activity, end_activity,
        attribute:<name> (case attribute) or event_attribute:<name>. Cells with fewer than 5 cases are withheld.
        Differences between cells describe recorded cases, not causes. Aggregates only.
        """
        if not isinstance(rows, str) or not rows:
            raise ToolError("rows must name a dimension")
        return _get("get_process_breakdown", f"/v1/process-intelligence/{_log(log_id)}/breakdown",
                    {"rows": rows, "columns": columns, "with_activity": with_activity, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def find_similar_process_cases(log_id: str, case_id: str, limit: int = 10,
                                   case_type: str | None = None) -> dict[str, Any]:
        """The recorded cases of a shared log whose sequence of activities is closest to case_id, with what each
        does more or less often, its duration and start, and the median duration of the cases found. Use it to
        ask "have we seen this before, and how did it go?"; follow up with compare_process_cases. Needs a log
        shared at the cases level. Similar paths do not mean the same cause.
        """
        if not isinstance(case_id, str) or not case_id:
            raise ToolError("case_id must be a case ID")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ToolError("limit must be 1 to 50")
        return _get("find_similar_process_cases", f"/v1/process-intelligence/{_log(log_id)}/similar-cases",
                    {"case_id": case_id, "limit": limit, "case_type": case_type})

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
