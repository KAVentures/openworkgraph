from __future__ import annotations

"""Read-only procedural memory derived from canonical OpenWorkGraph evidence.

This module deliberately creates no persistent learned state. Every result is
regenerated from canonical evidence and remains an explicitly non-authoritative
index. Human completion is not called success; agent success/failure is reported
only when the native structural evidence says so.
"""

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import re
from typing import Any, Iterable

from . import analytics
from .context_layers import candidate_tasks
from .db import rows


_MAX_EVENTS = 100_000
_DEFAULT_EVENTS = 25_000
_MAX_STEPS = 48
_EXPLICIT_FAILURES = frozenset({"error", "denied", "cancelled"})
_AGENT_TERMINAL = frozenset({"success", "error", "denied", "cancelled", "unknown"})
_HUMAN_FAMILY_PREFIXES = frozenset({"email", "github"})
_SAFE_AGENT_OPS = frozenset({
    "run_started", "run_finished", "model_call", "tool_call", "handoff",
    "human_approval_requested", "human_approval_received", "error",
})
_SAFE_TOOL_CATEGORIES = frozenset({
    "filesystem", "shell", "browser", "code", "search", "network", "database",
    "messaging", "issue_tracker", "deployment", "mcp", "other", "none",
})
_SAFE_HUMAN_ACTIONS = {
    "focus_control": "edit",
    "control_change": "change",
    "copy": "copy",
    "paste": "paste",
    "form_submit": "submit",
    "submit": "submit",
    "click": "click",
    "right_click": "right_click",
    "screen_click": "click",
    "screen_right_click": "right_click",
}
_KNOWN_SURFACES = {
    "gmail": "gmail",
    "outlook": "outlook",
    "github": "github",
    "slack": "slack",
    "jira": "jira",
    "linear": "linear",
    "notion": "notion",
    "google docs": "google-docs",
    "google sheets": "google-sheets",
    "google drive": "google-drive",
    "figma": "figma",
    "terminal": "terminal",
    "visual studio code": "vscode",
    "vs code": "vscode",
    "vscode": "vscode",
    "browser": "browser",
    "google chrome": "chrome",
    "chrome": "chrome",
    "safari": "safari",
    "microsoft edge": "edge",
    "edge": "edge",
    "firefox": "firefox",
    "chatgpt": "chatgpt",
    "claude": "claude",
    "codex": "codex",
    "openworkgraph": "openworkgraph",
}
_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_URL_RE = re.compile(r"(?:https?://|www\.)", re.I)
_LONG_ID_RE = re.compile(r"(?:\b\d{7,}\b|\b[0-9a-f]{16,}\b)", re.I)
_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")
_STEP_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")


def _hash(prefix: str, value: Any, *, size: int = 16) -> str:
    raw = str(value or "")
    return f"{prefix}:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _meta(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    return meta, trace


def _safe_name(value: Any, *, fallback_prefix: str) -> str:
    """Keep static-looking structural names; hash obvious dynamic/content labels."""
    raw = str(value or "").strip()
    low = raw.lower()
    if (
        not raw
        or len(raw) > 80
        or _EMAIL_RE.search(raw)
        or _URL_RE.search(raw)
        or _LONG_ID_RE.search(raw)
        or "/" in raw
        or "\\" in raw
        or not _SAFE_NAME_RE.fullmatch(low)
    ):
        return _hash(fallback_prefix, raw or "unknown", size=12)
    return low


def _surface_key(value: Any) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    known = _KNOWN_SURFACES.get(raw.lower())
    if known:
        return f"surface:{known}"
    return _hash("surface", raw or "unknown", size=12)


def _human_family(task: dict[str, Any], steps: list[str]) -> tuple[str, str]:
    raw = str(task.get("task_family") or "").strip().lower()
    if "." in raw:
        prefix = raw.split(".", 1)[0]
        if prefix in _HUMAN_FAMILY_PREFIXES and _FAMILY_KEY_RE.fullmatch(raw):
            return f"human:{raw}", "canonical_task_family"
    signature = "|".join(steps[:20]) or _surface_key(task.get("primary_surface"))
    return _hash("human:structure", signature, size=16), "structural_signature"


def _agent_family(events: list[dict[str, Any]], steps: list[str]) -> tuple[str, str]:
    workflow_id = ""
    framework = ""
    categories: list[str] = []
    for event in events:
        meta, trace = _meta(event)
        workflow_id = workflow_id or str(trace.get("workflow_id") or "").strip()
        agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
        framework = framework or str(agent.get("framework") or "").strip()
        tool = meta.get("tool") if isinstance(meta.get("tool"), dict) else {}
        category = str(tool.get("category") or "").lower()
        if category in _SAFE_TOOL_CATEGORIES and category != "none":
            if not categories or categories[-1] != category:
                categories.append(category)
    if workflow_id:
        return _hash("agent:workflow", workflow_id, size=16), "explicit_workflow_id"
    material = "|".join([
        _safe_name(framework, fallback_prefix="framework"),
        *categories[:16],
        *(step.split(":", 2)[0] for step in steps[:8]),
    ])
    return _hash("agent:structure", material or "unknown", size=16), "structural_signature"


def _event_ref(event_id: Any) -> str:
    return _hash("event", event_id or "unknown", size=16)


def _execution_id(kind: str, native: Any, started_at: Any) -> str:
    return _hash("execution", f"{kind}|{native}|{started_at}", size=16)


def _coarse_human_action(event: dict[str, Any]) -> str:
    if not str(event.get("event_type") or "").startswith(("browser_", "screen_")):
        return ""
    try:
        action = str(analytics._semantic_action(event) or "").strip().lower()
    except Exception:
        return ""
    return _SAFE_HUMAN_ACTIONS.get(action, "")


def _human_steps(
    task: dict[str, Any],
    session_events: list[dict[str, Any]],
) -> list[str]:
    start = _ts(task.get("started_at"))
    end = _ts(task.get("ended_at"))
    if start is None or end is None:
        return []
    out: list[str] = []
    for event in session_events:
        when = _ts(event.get("observed_at"))
        if when is None or when < start - 0.001 or when > end + 0.001:
            continue
        if event.get("event_type") == "focus_span" or str(event.get("event_type") or "").startswith(("browser_", "screen_")):
            try:
                surface = analytics._semantic_surface(event) if str(event.get("event_type") or "").startswith(("browser_", "screen_")) else event.get("app")
            except Exception:
                surface = event.get("app")
            surface_step = _surface_key(surface)
            if surface_step and (not out or out[-1] != surface_step):
                out.append(surface_step)
        action = _coarse_human_action(event)
        if action:
            token = f"action:{action}"
            if not out or out[-1] != token:
                out.append(token)
        if len(out) >= _MAX_STEPS:
            break
    return out


def _agent_step(event: dict[str, Any]) -> str:
    meta, _trace = _meta(event)
    operation = str(meta.get("operation") or "").strip().lower()
    status = str(meta.get("status") or "unknown").strip().lower()
    if operation not in _SAFE_AGENT_OPS or operation in {"run_started", "run_finished"}:
        return ""
    if operation == "model_call":
        return "model_call"
    if operation == "tool_call":
        tool = meta.get("tool") if isinstance(meta.get("tool"), dict) else {}
        category = str(tool.get("category") or "other").strip().lower()
        if category not in _SAFE_TOOL_CATEGORIES:
            category = "other"
        name = _safe_name(tool.get("name"), fallback_prefix="tool")
        suffix = f":{status}" if status in _EXPLICIT_FAILURES else ""
        return f"tool:{category}:{name}{suffix}"
    if operation == "handoff":
        return "handoff"
    if operation == "human_approval_requested":
        return "approval_request"
    if operation == "human_approval_received":
        decision = status if status in {"success", "denied", "cancelled"} else "observed"
        return f"approval_received:{decision}"
    if operation == "error":
        failure = status if status in _EXPLICIT_FAILURES else "error"
        return f"error:{failure}"
    return ""


def _agent_outcome(events: list[dict[str, Any]]) -> tuple[str, str]:
    finished: list[str] = []
    explicit_failures: list[str] = []
    for event in events:
        meta, _trace = _meta(event)
        operation = str(meta.get("operation") or "").lower()
        status = str(meta.get("status") or "unknown").lower()
        if operation == "run_finished" and status in _AGENT_TERMINAL:
            finished.append(status)
        if status in _EXPLICIT_FAILURES and operation in {"run_finished", "error", "tool_call"}:
            explicit_failures.append(status)
    if finished:
        value = finished[-1]
        return value, "explicit_run_terminal_status"
    if explicit_failures:
        return explicit_failures[-1], "explicit_failure_evidence"
    return "unknown", "no_terminal_outcome_observed"


def _approval_points(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    points: list[dict[str, str]] = []
    last_structural = "run_start"
    pending: int | None = None
    for event in events:
        meta, _trace = _meta(event)
        operation = str(meta.get("operation") or "").lower()
        status = str(meta.get("status") or "unknown").lower()
        step = _agent_step(event)
        if operation == "human_approval_requested":
            points.append({"preceding_step": last_structural, "decision_status": "unobserved"})
            pending = len(points) - 1
        elif operation == "human_approval_received" and pending is not None:
            points[pending]["decision_status"] = status if status in {"success", "denied", "cancelled"} else "observed"
            pending = None
        if step and operation not in {"human_approval_requested", "human_approval_received"}:
            last_structural = step
    return points


def derive_executions(raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build privacy-minimized human and agent executions from canonical evidence."""
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in raw_events:
        by_session[str(event.get("session_id") or "")].append(event)
    for events in by_session.values():
        events.sort(key=lambda e: str(e.get("observed_at") or ""))

    output: list[dict[str, Any]] = []
    task_data = candidate_tasks(limit=max(1, len(raw_events)), _raw_events=raw_events)
    for task in task_data.get("tasks") or []:
        session_id = str(task.get("session_id") or "")
        steps = _human_steps(task, by_session.get(session_id, []))
        if not steps:
            continue
        family_key, family_basis = _human_family(task, steps)
        observed_completion = bool(task.get("completion_observed"))
        output.append({
            "execution_id": _execution_id("human", task.get("task_id") or session_id, task.get("started_at")),
            "actor_kind": "human",
            "family_key": family_key,
            "family_basis": family_basis,
            "started_at": task.get("started_at"),
            "ended_at": task.get("ended_at"),
            "duration_seconds": round(float(task.get("elapsed_seconds") or 0), 3),
            "outcome_status": "observed_completion" if observed_completion else "unknown",
            "outcome_basis": "strong_completion_anchor" if observed_completion else "no_strong_completion_anchor",
            "positive_example": observed_completion,
            "explicit_failure": False,
            "steps": steps[:_MAX_STEPS],
            "observation_level": "human_observed",
            "evidence_refs": [_event_ref(value) for value in list(task.get("anchor_event_ids") or [])[:12]],
            "evidence_window": dict(task.get("evidence_window") or {}),
            "derived": True,
            "authoritative": False,
            "needs_review": True,
            "_approval_points": [],
        })

    agent_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in raw_events:
        if str(event.get("source") or "") != "agent" and not str(event.get("event_type") or "").startswith("agent_"):
            continue
        meta, trace = _meta(event)
        native_run = str(trace.get("run_id") or trace.get("trace_id") or event.get("session_id") or "").strip()
        if not native_run:
            continue
        actor = str(event.get("actor_id") or "agent")
        agent_groups[(actor, native_run)].append(event)

    for (actor, native_run), events in agent_groups.items():
        events.sort(key=lambda e: str(e.get("observed_at") or ""))
        steps: list[str] = []
        for event in events:
            token = _agent_step(event)
            if token and (not steps or steps[-1] != token):
                steps.append(token)
            if len(steps) >= _MAX_STEPS:
                break
        if not steps:
            continue
        family_key, family_basis = _agent_family(events, steps)
        outcome, outcome_basis = _agent_outcome(events)
        start = events[0].get("observed_at")
        end = events[-1].get("observed_at")
        start_ts = _ts(start)
        end_ts = _ts(end)
        duration = max(0.0, (end_ts or 0) - (start_ts or 0)) if start_ts is not None and end_ts is not None else 0.0
        first_meta, _first_trace = _meta(events[0])
        observation_level = str(first_meta.get("observation_level") or "unknown")
        output.append({
            "execution_id": _execution_id("agent", f"{actor}|{native_run}", start),
            "actor_kind": "agent",
            "family_key": family_key,
            "family_basis": family_basis,
            "started_at": start,
            "ended_at": end,
            "duration_seconds": round(duration, 3),
            "outcome_status": outcome,
            "outcome_basis": outcome_basis,
            "positive_example": outcome == "success",
            "explicit_failure": outcome in _EXPLICIT_FAILURES,
            "steps": steps[:_MAX_STEPS],
            "observation_level": observation_level,
            "evidence_refs": [_event_ref(event.get("event_id")) for event in events[:20]],
            "evidence_window": {"started_at": start, "ended_at": end},
            "derived": True,
            "authoritative": False,
            "needs_review": True,
            "_approval_points": _approval_points(events),
        })

    output.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return output


def _public_execution(execution: dict[str, Any]) -> dict[str, Any]:
    return {key: execution.get(key) for key in (
        "execution_id", "actor_kind", "family_key", "family_basis", "started_at", "ended_at",
        "duration_seconds", "outcome_status", "outcome_basis", "steps", "observation_level",
        "evidence_refs", "evidence_window", "derived", "authoritative", "needs_review",
    )}


def _sequence_similarity(query: list[str], candidate: list[str]) -> float:
    if not query:
        return 1.0
    if not candidate:
        return 0.0
    prefix = 0
    for a, b in zip(query, candidate):
        if a != b:
            break
        prefix += 1
    prefix_score = prefix / max(1, len(query))
    qset, cset = set(query), set(candidate)
    union = qset | cset
    jaccard = len(qset & cset) / len(union) if union else 0.0
    return round(0.65 * prefix_score + 0.35 * jaccard, 6)


def _family_summary(family_key: str, executions: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [item for item in executions if item.get("positive_example")]
    failures = [item for item in executions if item.get("explicit_failure")]
    unknown = [item for item in executions if item.get("outcome_status") == "unknown"]
    observed_completion = sum(1 for item in executions if item.get("outcome_status") == "observed_completion")
    explicit_success = sum(1 for item in executions if item.get("outcome_status") == "success")
    sequence_counts = Counter(tuple(item.get("steps") or []) for item in positive if item.get("steps"))
    dominant: tuple[str, ...] = ()
    dominant_support = 0
    if sequence_counts:
        dominant, dominant_support = sequence_counts.most_common(1)[0]
    basis = Counter(str(item.get("family_basis") or "unknown") for item in executions).most_common(1)[0][0]
    if len(positive) >= 5 and basis in {"canonical_task_family", "explicit_workflow_id"}:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "family_key": family_key,
        "actor_kind": executions[0].get("actor_kind") if executions else "unknown",
        "family_basis": basis,
        "execution_count": len(executions),
        "positive_example_count": len(positive),
        "observed_completion_count": observed_completion,
        "explicit_success_count": explicit_success,
        "explicit_failure_count": len(failures),
        "unknown_outcome_count": len(unknown),
        "dominant_sequence": list(dominant)[:_MAX_STEPS],
        "dominant_sequence_support": dominant_support,
        "dominant_sequence_fraction": round(dominant_support / len(positive), 4) if positive else 0.0,
        "reusable_candidate": len(positive) >= 2,
        "confidence": confidence,
        "derived": True,
        "authoritative": False,
        "needs_review": True,
    }


def procedural_overview(raw_events: list[dict[str, Any]], *, min_support: int = 2) -> dict[str, Any]:
    executions = derive_executions(raw_events)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for execution in executions:
        grouped[str(execution.get("family_key") or "")].append(execution)
    threshold = max(1, min(int(min_support), 100))
    families = [
        _family_summary(key, members)
        for key, members in grouped.items()
        if len(members) >= threshold
    ]
    families.sort(key=lambda item: (int(item["positive_example_count"]), int(item["execution_count"])), reverse=True)
    return {
        "families": families[:100],
        "family_count": len(families),
        "execution_count": len(executions),
        "minimum_support": threshold,
        "derived": True,
        "authoritative": False,
        "source": "canonical_evidence",
        "persisted_learned_state": False,
        "interpretation": "observed procedural families; not instructions or ground truth",
    }


def similar_runs(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str,
    current_steps: Iterable[str] = (),
    limit: int = 10,
) -> dict[str, Any]:
    if not _FAMILY_KEY_RE.fullmatch(family_key):
        raise ValueError("invalid family_key")
    query_steps = [str(value).strip().lower() for value in current_steps if str(value).strip()]
    if len(query_steps) > _MAX_STEPS or any(not _STEP_RE.fullmatch(step) for step in query_steps):
        raise ValueError("invalid current_steps")
    candidates = [item for item in derive_executions(raw_events) if item.get("family_key") == family_key]
    ranked = [(_sequence_similarity(query_steps, list(item.get("steps") or [])), item) for item in candidates]
    ranked.sort(key=lambda pair: (pair[0], str(pair[1].get("started_at") or "")), reverse=True)
    out = []
    for score, item in ranked[:max(1, min(int(limit), 50))]:
        public = _public_execution(item)
        public["similarity_score"] = score
        public["matched_on"] = "family_and_structural_steps" if query_steps else "family"
        out.append(public)
    return {
        "family_key": family_key,
        "runs": out,
        "returned": len(out),
        "derived": True,
        "authoritative": False,
        "source": "canonical_evidence",
    }


def failure_patterns(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str = "",
    min_support: int = 2,
) -> dict[str, Any]:
    if family_key and not _FAMILY_KEY_RE.fullmatch(family_key):
        raise ValueError("invalid family_key")
    executions = derive_executions(raw_events)
    if family_key:
        executions = [item for item in executions if item.get("family_key") == family_key]
    family_totals = Counter(str(item.get("family_key") or "") for item in executions)
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in executions:
        if not item.get("explicit_failure"):
            continue
        steps = list(item.get("steps") or [])
        failure_step = steps[-1] if steps else "unknown"
        preceding = steps[-2] if len(steps) >= 2 else "run_start"
        key = (str(item.get("family_key") or ""), preceding, failure_step, str(item.get("outcome_status") or "unknown"))
        grouped[key].append(item)
    threshold = max(2, min(int(min_support), 100))
    patterns = []
    for (family, preceding, failure_step, status), members in grouped.items():
        if len(members) < threshold:
            continue
        total = max(1, family_totals[family])
        patterns.append({
            "pattern_id": _hash("failure", "|".join((family, preceding, failure_step, status)), size=16),
            "family_key": family,
            "preceding_step": preceding,
            "failure_step": failure_step,
            "explicit_status": status,
            "support": len(members),
            "family_execution_count": total,
            "observed_fraction": round(len(members) / total, 4),
            "evidence_refs": [ref for item in members[:5] for ref in list(item.get("evidence_refs") or [])[:2]],
            "interpretation": "repeated explicit failure sequence; no causal claim",
            "derived": True,
            "authoritative": False,
            "needs_review": True,
        })
    patterns.sort(key=lambda item: (int(item["support"]), float(item["observed_fraction"])), reverse=True)
    return {
        "patterns": patterns[:100],
        "minimum_support": threshold,
        "derived": True,
        "authoritative": False,
        "explicit_failures_only": True,
    }


def next_steps(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str,
    prefix: Iterable[str] = (),
    after_step: str = "",
    min_support: int = 2,
) -> dict[str, Any]:
    if not _FAMILY_KEY_RE.fullmatch(family_key):
        raise ValueError("invalid family_key")
    prefix_steps = [str(value).strip().lower() for value in prefix if str(value).strip()]
    if len(prefix_steps) > _MAX_STEPS or any(not _STEP_RE.fullmatch(step) for step in prefix_steps):
        raise ValueError("invalid prefix")
    after = str(after_step or "").strip().lower()
    if after and not _STEP_RE.fullmatch(after):
        raise ValueError("invalid after_step")
    executions = [
        item for item in derive_executions(raw_events)
        if item.get("family_key") == family_key and item.get("positive_example")
    ]
    counts: Counter[str] = Counter()
    opportunities = 0
    for item in executions:
        steps = list(item.get("steps") or [])
        candidate = ""
        if prefix_steps:
            if len(steps) > len(prefix_steps) and steps[:len(prefix_steps)] == prefix_steps:
                candidate = steps[len(prefix_steps)]
        elif after:
            try:
                index = steps.index(after)
            except ValueError:
                continue
            if index + 1 < len(steps):
                candidate = steps[index + 1]
        elif steps:
            candidate = steps[0]
        if candidate:
            opportunities += 1
            counts[candidate] += 1
    threshold = max(2, min(int(min_support), 100))
    candidates = [
        {
            "step": step,
            "support": support,
            "opportunities": opportunities,
            "observed_fraction": round(support / opportunities, 4) if opportunities else 0.0,
            "interpretation": "observed next step among positive examples; not a recommendation",
        }
        for step, support in counts.most_common()
        if support >= threshold
    ]
    return {
        "family_key": family_key,
        "candidates": candidates[:30],
        "positive_execution_count": len(executions),
        "opportunities": opportunities,
        "minimum_support": threshold,
        "derived": True,
        "authoritative": False,
        "prescriptive": False,
    }


def approval_patterns(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str = "",
    min_support: int = 2,
) -> dict[str, Any]:
    if family_key and not _FAMILY_KEY_RE.fullmatch(family_key):
        raise ValueError("invalid family_key")
    executions = [item for item in derive_executions(raw_events) if item.get("actor_kind") == "agent"]
    if family_key:
        executions = [item for item in executions if item.get("family_key") == family_key]
    family_totals = Counter(str(item.get("family_key") or "") for item in executions)
    grouped: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, str]]]] = defaultdict(list)
    for item in executions:
        for point in item.get("_approval_points") or []:
            grouped[(str(item.get("family_key") or ""), str(point.get("preceding_step") or "run_start"))].append((item, point))
    threshold = max(2, min(int(min_support), 100))
    patterns = []
    for (family, preceding), records in grouped.items():
        execution_ids = {str(item.get("execution_id") or "") for item, _point in records}
        support = len(execution_ids)
        if support < threshold:
            continue
        decisions = Counter(str(point.get("decision_status") or "unobserved") for _item, point in records)
        total = max(1, family_totals[family])
        patterns.append({
            "pattern_id": _hash("approval", f"{family}|{preceding}", size=16),
            "family_key": family,
            "preceding_step": preceding,
            "executions_with_request": support,
            "family_execution_count": total,
            "observed_fraction": round(support / total, 4),
            "decision_status_counts": dict(sorted(decisions.items())),
            "normative_requirement": False,
            "interpretation": "observed approval-request hotspot; not proof that approval is required",
            "derived": True,
            "authoritative": False,
            "needs_review": True,
        })
    patterns.sort(key=lambda item: (int(item["executions_with_request"]), float(item["observed_fraction"])), reverse=True)
    return {
        "patterns": patterns[:100],
        "minimum_support": threshold,
        "derived": True,
        "authoritative": False,
        "normative_requirement_inferred": False,
    }


def load_recent_evidence(*, limit: int = _DEFAULT_EVENTS, since: str | None = None) -> list[dict[str, Any]]:
    page_limit = max(1, min(int(limit), _MAX_EVENTS))
    if since:
        raw = rows(
            "SELECT * FROM events WHERE observed_at >= ? ORDER BY observed_at DESC LIMIT ?",
            (since, page_limit),
        )
    else:
        raw = rows("SELECT * FROM events ORDER BY observed_at DESC LIMIT ?", (page_limit,))
    raw.reverse()
    return raw
