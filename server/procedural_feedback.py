from __future__ import annotations

"""Readable, privacy-safe presentation over stable procedural-memory identities.

The canonical procedural-memory ``steps`` remain unchanged and continue to define
structural family identities. This module derives a second human-facing vocabulary
from the same evidence so agents can understand observed procedures without
changing family keys or exposing arbitrary UI text, hostnames, URLs, names, or IDs.
"""

from collections import Counter, defaultdict
from statistics import median
import re
from typing import Any

from browser_utils import is_browser_app
from mcp_server.security import _looks_instruction_like
from normalizer import safe_action_label, safe_surface

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
    # Unknown desktop application names can contain customer/person/resource text.
    # Preserve only the stable pseudonym already used by structural memory.
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
    label = safe_action_label(target)
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
        # Family identity is calculated from the pre-existing structural sequence.
        # The readable semantic sequence below is presentation only.
        family_key, family_basis = memory._human_family(task, structural_steps)
        semantic_steps = _semantic_steps(task, session_events)
        observed_completion = bool(task.get("completion_observed"))
        output.append({
            "execution_id": memory._execution_id(
                "human", task.get("task_id") or session_id, task.get("started_at")
            ),
            "family_key": family_key,
            "family_basis": family_basis,
            "started_at": task.get("started_at"),
            "ended_at": task.get("ended_at"),
            "duration_seconds": round(float(task.get("elapsed_seconds") or 0), 3),
            "outcome_status": "observed_completion" if observed_completion else "unknown",
            "outcome_basis": (
                "strong_completion_anchor" if observed_completion else "no_strong_completion_anchor"
            ),
            "positive_example": observed_completion,
            "structural_steps": structural_steps[:_MAX_STEPS],
            "semantic_steps": semantic_steps[:_MAX_STEPS],
            "evidence_refs": [
                memory._event_ref(value)
                for value in list(task.get("anchor_event_ids") or [])[:8]
            ],
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
    for step in requested:
        _validate_readable_probe(step)
        exact = lookup.get(step.casefold())
        if exact:
            resolved.append(exact)
        else:
            unknown.append(step)
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
                "Legacy structural tokens remain supported by the standard procedural-memory views."
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
                "outcome_status", "outcome_basis", "family_basis", "structural_steps",
                "semantic_steps", "evidence_refs", "derived", "authoritative", "needs_review",
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


__all__ = ["readable_feedback", "_human_runs", "_semantic_steps"]
