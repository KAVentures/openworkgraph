from __future__ import annotations

"""Readable, privacy-safe presentation over stable procedural-memory identities.

The canonical procedural-memory ``steps`` remain unchanged and continue to define
structural family identities. This module derives a second human-facing vocabulary
from the same evidence so agents can understand observed procedures without
changing family keys or exposing arbitrary UI text, hostnames, URLs, names, or IDs.
"""

from collections import Counter, defaultdict
import copy
from statistics import median
import re
from typing import Any

from browser_utils import is_browser_app
from mcp_server.security import _looks_instruction_like
from normalizer import safe_action_label, safe_surface
from semantic_actions import safe_semantic_action_label

from .context_layers import candidate_tasks
from .dashboard_privacy_policy import safe_dashboard_action
from . import procedural_memory as memory


_MAX_STEPS = 48
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_URL_RE = re.compile(r"(?:https?://|www\.)", re.I)
_LONG_ID_RE = re.compile(r"(?:\b\d{7,}\b|\b[0-9a-f]{16,}\b)", re.I)
_PASSIVE_ACTIONS = frozenset({
    "Page view", "Tab activated", "Navigation", "Scroll", "Observed action",
})
_SURFACE_DISPLAY = {
    "gmail": "Gmail",
    "outlook": "Outlook",
    "github": "GitHub",
    "slack": "Slack",
    "jira": "Jira",
    "linear": "Linear",
    "notion": "Notion",
    "google-docs": "Google Docs",
    "google-sheets": "Google Sheets",
    "google-drive": "Google Drive",
    "figma": "Figma",
    "terminal": "Terminal",
    "vscode": "VS Code",
    "browser": "Browser",
    "chrome": "Browser",
    "safari": "Browser",
    "edge": "Browser",
    "firefox": "Browser",
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "codex": "Codex",
    "openworkgraph": "OpenWorkGraph",
}
_PAIR_SEPARATOR_RE = re.compile(r"\s*(?:·|\s[-–—]\s)\s*", re.UNICODE)


def _event_meta(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("metadata")
    return value if isinstance(value, dict) else {}


def _safe_desktop_surface(app: Any) -> str:
    raw = str(app or "").strip()
    if is_browser_app(raw):
        return "Browser"
    token = memory._surface_key(raw)
    suffix = token.split(":", 1)[1] if ":" in token else "unknown"
    if suffix in _SURFACE_DISPLAY:
        return _SURFACE_DISPLAY[suffix]
    return f"Desktop app {suffix}"


def _safe_event_surface(event: dict[str, Any], *, last_browser_surface: str = "") -> str:
    event_type = str(event.get("event_type") or "")
    app = str(event.get("app") or "")
    meta = _event_meta(event)
    page = meta.get("page") if isinstance(meta.get("page"), dict) else {}

    if event_type.startswith("browser_"):
        return safe_surface(
            app=app,
            hostname=str(page.get("hostname") or ""),
            pathname=str(page.get("pathname") or ""),
            title=str(page.get("title") or event.get("window_title") or ""),
        )
    if event_type.startswith("screen_") and is_browser_app(app) and last_browser_surface:
        return last_browser_surface
    return _safe_desktop_surface(app)


def _safe_event_action(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    meta = _event_meta(event)
    target = meta.get("target") if isinstance(meta.get("target"), dict) else {}
    label = safe_semantic_action_label(target) or safe_action_label(target)
    if not label:
        label = safe_dashboard_action(
            meta.get("interaction"),
            meta.get("action"),
            event_type=event_type,
        )
    if label in _PASSIVE_ACTIONS:
        return ""
    return label


def _semantic_steps(task: dict[str, Any], session_events: list[dict[str, Any]]) -> list[str]:
    start = memory._ts(task.get("started_at"))
    end = memory._ts(task.get("ended_at"))
    if start is None or end is None:
        return []

    out: list[str] = []
    last_browser_surface = ""
    for event in session_events:
        when = memory._ts(event.get("observed_at"))
        if when is None or when < start - 0.001 or when > end + 0.001:
            continue
        event_type = str(event.get("event_type") or "")
        if not event_type.startswith(("browser_", "screen_")):
            continue
        surface = _safe_event_surface(event, last_browser_surface=last_browser_surface)
        if event_type.startswith("browser_") and surface and surface != "Browser":
            last_browser_surface = surface
        action = _safe_event_action(event)
        if not action:
            continue
        step = f"{surface} · {action}"
        if not out or out[-1] != step:
            out.append(step)
        if len(out) >= _MAX_STEPS:
            break
    return out


def _window_event_refs(task: dict[str, Any], session_events: list[dict[str, Any]]) -> list[str]:
    start = memory._ts(task.get("started_at"))
    end = memory._ts(task.get("ended_at"))
    refs: list[str] = []
    seen: set[str] = set()
    for value in list(task.get("anchor_event_ids") or []):
        ref = memory._event_ref(value)
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    if start is None or end is None:
        return refs[:8]
    for event in session_events:
        when = memory._ts(event.get("observed_at"))
        if when is None or when < start - 0.001 or when > end + 0.001:
            continue
        ref = memory._event_ref(event.get("event_id"))
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
        if len(refs) >= 8:
            break
    return refs[:8]


def _human_runs(raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in raw_events:
        by_session[str(event.get("session_id") or "")].append(event)
    for events in by_session.values():
        events.sort(key=lambda item: str(item.get("observed_at") or ""))

    output: list[dict[str, Any]] = []
    task_data = candidate_tasks(limit=max(1, len(raw_events)), _raw_events=raw_events)
    for task in task_data.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        session_id = str(task.get("session_id") or "")
        session_events = by_session.get(session_id, [])
        structural_steps = memory._human_steps(task, session_events)
        if not structural_steps:
            continue
        family_key, family_basis = memory._human_family(task, structural_steps)
        semantic_steps = _semantic_steps(task, session_events)
        observed_completion = bool(task.get("completion_observed"))
        started_at = task.get("started_at")
        ended_at = task.get("ended_at")
        output.append({
            "execution_id": memory._execution_id(
                "human", task.get("task_id") or session_id, started_at
            ),
            "family_key": family_key,
            "family_basis": family_basis,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": round(float(task.get("elapsed_seconds") or 0), 3),
            "outcome_status": "observed_completion" if observed_completion else "unknown",
            "outcome_basis": (
                "strong_completion_anchor" if observed_completion else "no_strong_completion_anchor"
            ),
            "positive_example": observed_completion,
            "structural_steps": structural_steps[:_MAX_STEPS],
            "semantic_steps": semantic_steps[:_MAX_STEPS],
            "evidence_refs": _window_event_refs(task, session_events),
            "evidence_window": {"started_at": started_at, "ended_at": ended_at},
            "trace_lookup": {
                "tool": "get_workflow_trace",
                "since": started_at,
                "until": ended_at,
                "scope": "all",
            },
            "derived": True,
            "authoritative": False,
            "needs_review": True,
        })
    output.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return output


def _split_steps(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = [part.strip() for part in re.split(r"\s*(?:,|→)\s*", raw) if part.strip()]
    if len(parts) > _MAX_STEPS:
        raise ValueError("too many readable steps")
    return parts


def _canonical_readable_alias(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    pieces = _PAIR_SEPARATOR_RE.split(raw, maxsplit=1)
    if len(pieces) == 2 and pieces[0].strip() and pieces[1].strip():
        return f"{pieces[0].strip()} · {pieces[1].strip()}"
    return raw


def _validate_readable_probe(value: str) -> None:
    probe = str(value or "").strip()
    instruction_probe = re.sub(r"[·:._/-]+", " ", probe)
    if (
        not probe
        or len(probe) > 200
        or bool(_EMAIL_RE.search(probe))
        or bool(_URL_RE.search(probe))
        or bool(_LONG_ID_RE.search(probe))
        or _looks_instruction_like(instruction_probe)
    ):
        raise ValueError("unsafe readable step")


def _available_semantic_steps(runs: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for run in runs:
        for step in run.get("semantic_steps") or []:
            key = str(step).casefold()
            if key and key not in seen:
                seen.add(key)
                output.append(str(step))
    return output[:100]


def _resolve_semantic_steps(
    value: str,
    *,
    valid_steps: list[str],
) -> tuple[str, list[str], list[str]]:
    requested = _split_steps(value)
    if not requested:
        return "ok", [], []
    lookup = {step.casefold(): step for step in valid_steps}
    resolved: list[str] = []
    unknown: list[str] = []
    for raw_step in requested:
        step = _canonical_readable_alias(raw_step)
        _validate_readable_probe(step)
        exact = lookup.get(step.casefold())
        if exact:
            resolved.append(exact)
        else:
            unknown.append(raw_step)
    return ("ok" if not unknown else "unrecognized_step"), resolved, unknown


def _semantic_similarity(query: list[str], candidate: list[str]) -> float:
    if not query:
        return 1.0
    q = [item.casefold() for item in query]
    c = [item.casefold() for item in candidate]
    return memory._sequence_similarity(q, c)


def readable_feedback(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str,
    current_steps: str = "",
    after_step: str = "",
    min_support: int = 2,
    run_limit: int = 8,
) -> dict[str, Any]:
    """Return human-readable observed feedback without changing procedural identity."""
    if not memory._FAMILY_KEY_RE.fullmatch(str(family_key or "")):
        raise ValueError("invalid family_key")
    family_runs = [run for run in _human_runs(raw_events) if run.get("family_key") == family_key]
    valid_steps = _available_semantic_steps(family_runs)

    current_status, current, unknown_current = _resolve_semantic_steps(
        current_steps, valid_steps=valid_steps
    )
    after_status, after_values, unknown_after = _resolve_semantic_steps(
        after_step, valid_steps=valid_steps
    )
    status = "ok"
    unknown = [*unknown_current, *unknown_after]
    if current_status != "ok" or after_status != "ok":
        status = "unrecognized_step"
    if status != "ok":
        return {
            "status": status,
            "family_key": family_key,
            "unrecognized_steps": unknown,
            "valid_semantic_steps": valid_steps,
            "instruction": (
                "Use an exact valid semantic step shown here, or omit current_steps/after_step. "
                "Readable separators '·', '-', '–' and '—' are accepted. Legacy structural "
                "tokens remain supported by the standard procedural-memory views."
            ),
            "runs": [],
            "returned": 0,
            "next_steps": {"candidates": []},
            "derived": True,
            "authoritative": False,
            "prescriptive": False,
        }

    after = after_values[0] if after_values else ""
    ranked: list[tuple[float, dict[str, Any]]] = []
    for run in family_runs:
        score = _semantic_similarity(current, list(run.get("semantic_steps") or []))
        ranked.append((score, run))
    ranked.sort(key=lambda pair: (pair[0], str(pair[1].get("started_at") or "")), reverse=True)

    cap = max(1, min(int(run_limit), 25))
    public_runs: list[dict[str, Any]] = []
    for score, run in ranked[:cap]:
        public_runs.append({
            key: run.get(key)
            for key in (
                "execution_id", "started_at", "ended_at", "duration_seconds",
                "outcome_status", "outcome_basis", "family_basis", "semantic_steps",
                "evidence_refs", "evidence_window", "trace_lookup", "derived",
                "authoritative", "needs_review",
            )
        } | {
            "similarity_score": score,
            "matched_on": "family_and_semantic_steps" if current else "family",
        })

    positives = [run for run in family_runs if run.get("positive_example")]
    counts: Counter[str] = Counter()
    opportunities = 0
    for run in positives:
        steps = list(run.get("semantic_steps") or [])
        candidate = ""
        if current:
            lowered = [value.casefold() for value in steps]
            prefix = [value.casefold() for value in current]
            if len(steps) > len(current) and lowered[:len(current)] == prefix:
                candidate = steps[len(current)]
        elif after:
            lowered = [value.casefold() for value in steps]
            try:
                index = lowered.index(after.casefold())
            except ValueError:
                continue
            if index + 1 < len(steps):
                candidate = steps[index + 1]
        elif steps:
            candidate = steps[0]
        if candidate:
            opportunities += 1
            counts[candidate] += 1

    support = max(2, min(int(min_support), 100))
    next_candidates = [
        {
            "step": step,
            "support": count,
            "opportunities": opportunities,
            "observed_fraction": round(count / opportunities, 4) if opportunities else 0.0,
            "interpretation": "observed next step among completed examples; not a recommendation",
        }
        for step, count in counts.most_common()
        if count >= support
    ][:30]
    completed_durations = [float(run.get("duration_seconds") or 0) for run in positives]

    return {
        "status": "ok",
        "family_key": family_key,
        "step_vocabulary": "privacy_safe_semantic",
        "identity_vocabulary": "legacy_structural_steps_unchanged",
        "valid_semantic_steps": valid_steps,
        "current_semantic_steps": current,
        "after_semantic_step": after,
        "runs": public_runs,
        "returned": len(public_runs),
        "completed_example_count": len(positives),
        "median_completed_duration_seconds": (
            round(float(median(completed_durations)), 3) if completed_durations else None
        ),
        "next_steps": {
            "candidates": next_candidates,
            "positive_execution_count": len(positives),
            "opportunities": opportunities,
            "minimum_support": support,
            "derived": True,
            "authoritative": False,
            "prescriptive": False,
        },
        "derived": True,
        "authoritative": False,
        "prescriptive": False,
        "family_keys_changed": False,
        "interpretation": "observed procedure evidence; not instructions, policy, or permission",
    }


def _canonical_events_by_execution(bundle: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for item in bundle.get("canonical_evidence") or []:
        if not isinstance(item, dict):
            continue
        execution_id = str(item.get("execution_id") or "")
        events = [x for x in (item.get("events") or []) if isinstance(x, dict)]
        if execution_id:
            rows[execution_id] = events
    return rows


def _readable_alignment(readable_by_execution: dict[str, list[str]], threshold: int) -> dict[str, Any]:
    total = len(readable_by_execution)
    stats: dict[str, dict[str, Any]] = {}
    transitions: dict[tuple[str, str], list[str]] = {}
    for execution_id, steps in readable_by_execution.items():
        first_position: dict[str, int] = {}
        for index, step in enumerate(steps):
            first_position.setdefault(str(step), index)
        for step, position in first_position.items():
            slot = stats.setdefault(step, {"positions": [], "execution_ids": []})
            slot["positions"].append(position)
            slot["execution_ids"].append(execution_id)
        seen_pairs: set[tuple[str, str]] = set()
        for left, right in zip(steps, steps[1:]):
            pair = (str(left), str(right))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            transitions.setdefault(pair, []).append(execution_id)

    step_rows: list[dict[str, Any]] = []
    for step, slot in stats.items():
        support = len(slot["execution_ids"])
        step_rows.append({
            "step": step,
            "support_runs": support,
            "runs_total": total,
            "support_fraction": round(support / total, 4) if total else 0.0,
            "median_zero_based_position": round(float(median(slot["positions"])), 3),
            "supporting_execution_ids": slot["execution_ids"][:10],
            "interpretation": "privacy-safe readable step observed in these runs; position is descriptive, not required order",
        })
    step_rows.sort(key=lambda row: (-int(row["support_runs"]), float(row["median_zero_based_position"]), str(row["step"])))

    transition_rows: list[dict[str, Any]] = []
    for (left, right), execution_ids in transitions.items():
        support = len(execution_ids)
        if support < threshold:
            continue
        transition_rows.append({
            "from_step": left,
            "to_step": right,
            "support_runs": support,
            "runs_total": total,
            "support_fraction": round(support / total, 4) if total else 0.0,
            "supporting_execution_ids": execution_ids[:10],
            "interpretation": "adjacent privacy-safe readable transition observed; not a prescribed transition",
        })
    transition_rows.sort(key=lambda row: (-int(row["support_runs"]), str(row["from_step"]), str(row["to_step"])))
    return {
        "method": "privacy-safe semantic steps derived from canonical evidence; no semantic workflow meaning is inferred",
        "step_vocabulary": "privacy_safe_semantic",
        "high_support_minimum_runs": threshold,
        "high_support_steps": [row for row in step_rows if int(row["support_runs"]) >= threshold],
        "less_common_observed_steps": [row for row in step_rows if int(row["support_runs"]) < threshold],
        "high_support_adjacent_transitions": transition_rows,
        "common_path_claimed": False,
    }


def _discovery_handoff_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Project generic workflow evidence into a label-free, readable Discovery handoff."""
    result = copy.deepcopy(bundle)
    events_by_execution = _canonical_events_by_execution(bundle)
    readable_by_execution: dict[str, list[str]] = {}
    source_executions = {
        str(item.get("execution_id") or ""): item
        for item in bundle.get("executions") or []
        if isinstance(item, dict)
    }
    for execution_id, execution in source_executions.items():
        readable_by_execution[execution_id] = _semantic_steps(
            execution,
            events_by_execution.get(execution_id, []),
        )

    for execution in result.get("executions") or []:
        if not isinstance(execution, dict):
            continue
        execution_id = str(execution.get("execution_id") or "")
        execution.pop("family_key", None)
        execution.pop("family_basis", None)
        execution["steps"] = readable_by_execution.get(execution_id, [])
        execution["step_vocabulary"] = "privacy_safe_semantic"

    selector = result.get("selector") if isinstance(result.get("selector"), dict) else {}
    selector.pop("family_key", None)
    selector.pop("family_keys_present", None)
    selector["family_label_included"] = False
    selector["family_grouping_used_for_navigation_only"] = True

    raw_alignment = bundle.get("structural_alignment") if isinstance(bundle.get("structural_alignment"), dict) else {}
    threshold = max(1, int(raw_alignment.get("high_support_minimum_runs") or 1))
    result["format"] = "openworkgraph.discovery-workflow-evidence.v1"
    result["structural_alignment"] = _readable_alignment(readable_by_execution, threshold)
    result["discovery_presentation"] = {
        "readable_steps_first": True,
        "step_vocabulary": "privacy_safe_semantic",
        "derived_family_label_omitted": True,
        "machine_structural_tokens_omitted": True,
        "canonical_evidence_preserved": bool(result.get("canonical_evidence_included")),
    }
    return result


def _format_gap_duration(seconds: float) -> str:
    value = max(0.0, float(seconds or 0.0))
    if value <= 0:
        return ""
    if value < 60:
        return f"about {max(1, int(round(value)))} seconds"
    minutes = value / 60.0
    rounded = round(minutes)
    if abs(minutes - rounded) < 0.15:
        return f"about {int(rounded)} minute" + ("" if int(rounded) == 1 else "s")
    return f"about {minutes:.1f} minutes"


def _scope_gap_question(bundle: dict[str, Any]) -> dict[str, Any] | None:
    executions = {
        str(item.get("execution_id") or ""): item
        for item in bundle.get("executions") or []
        if isinstance(item, dict)
    }
    total = len(executions)
    if not total:
        return None
    affected: list[str] = []
    durations: list[float] = []
    next_steps: list[str] = []
    for execution_id, events in _canonical_events_by_execution(bundle).items():
        gaps = [event for event in events if str(event.get("event_type") or "") == "discovery_scope_gap"]
        if not gaps or execution_id not in executions:
            continue
        affected.append(execution_id)
        duration = sum(max(0.0, float(event.get("duration_seconds") or 0.0)) for event in gaps)
        if duration > 0:
            durations.append(duration)
        last_gap_at = max((str(event.get("observed_at") or "") for event in gaps), default="")
        execution = executions[execution_id]
        if last_gap_at and execution.get("ended_at"):
            after = {"started_at": last_gap_at, "ended_at": execution.get("ended_at")}
            readable = _semantic_steps(after, events)
            if readable:
                next_steps.append(readable[0])
    if not affected:
        return None

    duration_phrase = _format_gap_duration(float(median(durations))) if durations else ""
    next_step = Counter(next_steps).most_common(1)[0][0] if next_steps else ""
    where = f" before {next_step}" if next_step else " before continuing the scoped workflow"
    timing = f" for {duration_phrase}" if duration_phrase else ""
    support = len(affected)
    return {
        "question_key": f"scope-gap|{support}|{total}|{int(round(float(median(durations)))) if durations else 0}|{next_step}",
        "question": (
            f"In {support} of {total} selected runs, the observed work left the configured Discovery scope"
            f"{timing}{where}. What happened there, and is it a business rule, approval, exception, or unrelated work?"
        ),
        "reason": "content_free_scope_gap",
        "related_execution_ids": affected[:12],
        "derived": True,
        "authoritative": False,
        "privacy_note": "The marker contains timing only; the out-of-scope app/site/content was not stored.",
    }


def _review_candidates_with_readable_steps(
    families: dict[str, Any], bundles: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    presented = [_discovery_handoff_bundle(bundle) for bundle in bundles]
    by_execution: dict[str, list[str]] = {}
    for bundle in presented:
        readable = [
            str(row.get("step") or "")
            for row in (bundle.get("structural_alignment") or {}).get("high_support_steps") or []
            if str(row.get("step") or "")
        ]
        for execution in bundle.get("executions") or []:
            execution_id = str(execution.get("execution_id") or "")
            if execution_id:
                by_execution[execution_id] = readable
    rows = copy.deepcopy(list(families.get("families") or []))
    for family in rows:
        ids = [str(x) for x in family.get("execution_ids") or []]
        readable = next((by_execution[x] for x in ids if x in by_execution and by_execution[x]), [])
        family["readable_steps"] = readable
    return rows


def _suggested_questions(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bundle in bundles:
        executions = bundle.get("executions") or []
        ids = [str(x.get("execution_id") or "") for x in executions if isinstance(x, dict)]
        total = len(ids)
        gap = _scope_gap_question(bundle)
        if gap is not None and str(gap.get("question_key") or "") not in seen:
            seen.add(str(gap.get("question_key") or ""))
            rows.append(gap)
            if len(rows) >= 12:
                return rows
        presented = _discovery_handoff_bundle(bundle)
        alignment = presented.get("structural_alignment") if isinstance(presented.get("structural_alignment"), dict) else {}
        for item in (alignment.get("less_common_observed_steps") or [])[:4]:
            if not isinstance(item, dict):
                continue
            step = str(item.get("step") or "").strip()
            support = int(item.get("support_runs") or 0)
            if not step or support <= 0 or not total:
                continue
            key = f"{step}|{support}|{total}"
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "question_key": key,
                "question": (
                    f"We observed {step} in {support} of {total} selected runs. "
                    "What caused that variation, and does it represent a business rule, exception, or noise?"
                ),
                "reason": "observed_structural_variation",
                "related_execution_ids": ids[:12],
                "derived": True,
                "authoritative": False,
            })
            if len(rows) >= 12:
                return rows
    return rows




__all__ = [
    "readable_feedback",
    "_human_runs",
    "_semantic_steps",
    "_discovery_handoff_bundle",
    "_review_candidates_with_readable_steps",
    "_scope_gap_question",
    "_suggested_questions",
]