from __future__ import annotations

"""Join human work to agent runs: what the person did during and after each run.

Both streams are already in the local store; this only lines them up in time.

* ``during``: while the agent ran, how much the person was engaged, away, and
  in which *kind* of app (terminal, editor, browser, communication, AI
  assistant, other). App categories only: no titles, no app names.
* ``after``: from the end of a run to the next turn in the same session (at most
  two hours), the same measures, plus what the hook saw change in the project
  between the turns (``between_turns``: files changed, how many of them the
  agent had edited, whether HEAD moved).

Human capture and agent observation are separate choices; when there is no
human evidence in a window, the fields say so rather than implying zero.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

MAX_AFTER_SECONDS = 2 * 3600
_CATEGORIES = (
    ("terminal", ("terminal", "iterm", "warp", "alacritty", "kitty", "ghostty", "wezterm", "hyper", "powershell", "cmd.exe", "konsole", "tmux")),
    ("editor", ("visual studio code", "code", "cursor", "pycharm", "intellij", "webstorm", "xcode", "sublime", "vim", "emacs", "zed",
                "android studio", "rider", "goland", "clion", "fleet", "windsurf", "nova", "phpstorm", "rubymine", "datagrip")),
    ("ai_assistant", ("claude", "chatgpt", "copilot", "gemini", "perplexity")),
    ("browser", ("chrome", "safari", "firefox", "arc", "edge", "brave", "opera", "vivaldi", "chromium")),
    ("communication", ("slack", "teams", "mail", "outlook", "zoom", "discord", "messages", "telegram", "whatsapp", "meet", "signal")),
)


def app_category(app: Any) -> str:
    name = str(app or "").strip().lower()
    if not name:
        return "other"
    for category, needles in _CATEGORIES:
        for needle in needles:
            if name == needle or name.startswith(needle + " ") or (len(needle) > 4 and needle in name):
                return category
    return "other"


def _ts(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed.timestamp() if parsed.tzinfo else None


def _human_spans(raw_events: list[dict[str, Any]]) -> list[tuple[float, float, str, float]]:
    """(start, end, category, engaged fraction) for human focus and away spans."""
    spans: list[tuple[float, float, str, float]] = []
    for event in raw_events:
        if str(event.get("source") or "") == "agent" or str(event.get("event_type") or "").startswith("agent_"):
            continue
        kind = str(event.get("event_type") or "")
        if kind not in {"focus_span", "away_span"}:
            continue
        start = _ts(event.get("observed_at"))
        duration = float(event.get("duration_seconds") or 0)
        if start is None or duration <= 0:
            continue
        if kind == "away_span":
            spans.append((start, start + duration, "away", 0.0))
            continue
        meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        activity = meta.get("activity") if isinstance(meta.get("activity"), dict) else {}
        try:
            engaged = float(activity.get("engaged_seconds"))
            fraction = max(0.0, min(1.0, engaged / duration))
        except Exception:
            fraction = 1.0
        spans.append((start, start + duration, app_category(event.get("app")), fraction))
    spans.sort()
    return spans


def _window(spans: list[tuple[float, float, str, float]], start: float, end: float) -> dict[str, Any] | None:
    if end <= start:
        return None
    by_category: dict[str, float] = defaultdict(float)
    engaged = away = observed = 0.0
    for s, e, category, fraction in spans:
        if e <= start:
            continue
        if s >= end:
            break
        overlap = min(e, end) - max(s, start)
        if overlap <= 0:
            continue
        observed += overlap
        if category == "away":
            away += overlap
        else:
            engaged += overlap * fraction
            by_category[category] += overlap * fraction
    if observed <= 0:
        return {"human_capture_observed": False}
    return {
        "human_capture_observed": True,
        "engaged_seconds": round(engaged, 1),
        "away_seconds": round(away, 1),
        "by_category": {k: round(v, 1) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1]) if v >= 1},
    }


def human_context(groups: list[list[dict[str, Any]]], raw_events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Per agent-run group index: ``{"during": ..., "after": ...}`` when anything is known."""
    from .context_execution_linkage import _meta

    spans = _human_spans(raw_events)
    runs: list[tuple[int, str, float, float, dict[str, Any] | None]] = []
    children: set[int] = set()
    for index, events in enumerate(groups):
        start, end = _ts(events[0].get("observed_at")), _ts(events[-1].get("observed_at"))
        if start is None or end is None:
            continue
        _first_meta, first_trace = _meta(events[0])
        run_id = str(first_trace.get("run_id") or "")
        if ":sub:" in run_id or run_id.startswith("sub:"):
            children.add(index)  # a subagent run lives inside its parent's turn
        between = None
        for event in events:
            meta, _trace = _meta(event)
            if meta.get("operation") == "run_started" and isinstance(meta.get("between_turns"), dict):
                between = meta["between_turns"]
                break
        runs.append((index, str(events[0].get("session_id") or ""), start, end, between))

    by_session: dict[str, list[tuple[int, str, float, float, dict[str, Any] | None]]] = defaultdict(list)
    for run in runs:
        by_session[run[1]].append(run)

    out: dict[int, dict[str, Any]] = {}
    for session_runs in by_session.values():
        session_runs.sort(key=lambda r: r[2])
        for position, (index, _sid, start, end, _between) in enumerate(session_runs):
            context: dict[str, Any] = {}
            during = _window(spans, start, end) if spans else None
            if during is not None:
                context["during"] = during
            following = None if index in children else next(
                (r for r in session_runs[position + 1:] if r[2] >= end and r[0] not in children), None
            )
            if following is not None and following[2] - end <= MAX_AFTER_SECONDS:
                after = (_window(spans, end, following[2]) if spans else None) or {}
                after["gap_seconds"] = round(following[2] - end, 1)
                if following[4]:
                    after["between_turns"] = following[4]
                context["after"] = after
            if context:
                out[index] = context
    return out


__all__ = ["app_category", "human_context"]
