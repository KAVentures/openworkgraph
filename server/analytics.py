from __future__ import annotations

from collections import Counter, defaultdict
from bisect import bisect_left, bisect_right
import copy
import hashlib
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse
import re
from typing import Any

from .db import rows, normalized_rows, table_revision
from normalizer import normalize_event
from browser_utils import is_browser_app


def _workflow_label(e: dict[str, Any]) -> str:
    app = e.get("app") or "Unknown"
    title = (e.get("window_title") or "").strip()
    if is_browser_app(app) and title:
        if len(title) > 80:
            title = title[:77] + "…"
        return f"{app} · {title}"
    return app


def _event_rows(limit: int = 10000, since: str | None = None) -> list[dict[str, Any]]:
    if since:
        return rows(
            "SELECT * FROM events WHERE observed_at >= ? ORDER BY observed_at DESC LIMIT ?",
            (since, max(1, min(limit, 100000))),
        )[::-1]
    return rows(
        "SELECT * FROM events ORDER BY observed_at DESC LIMIT ?",
        (max(1, min(limit, 100000)),),
    )[::-1]


def _operational_event_rows(limit: int = 10000, since: str | None = None) -> list[dict[str, Any]]:
    """Read the privacy-safe operational twin, never the rich raw payload."""
    if since:
        return normalized_rows(
            "SELECT * FROM normalized_events WHERE observed_at >= ? ORDER BY observed_at DESC LIMIT ?",
            (since, max(1, min(limit, 100000))),
        )[::-1]
    return normalized_rows(
        "SELECT * FROM normalized_events ORDER BY observed_at DESC LIMIT ?",
        (max(1, min(limit, 100000)),),
    )[::-1]


def _interaction_label(e: dict[str, Any]) -> str:
    meta = e.get("metadata") or {}
    action = meta.get("action") or e.get("event_type", "interaction").replace("screen_", "")
    target = meta.get("target") or {}
    role = target.get("role") or target.get("subrole") or ""
    label = target.get("title") or target.get("description") or target.get("identifier") or ""
    if label:
        if len(str(label)) > 80:
            label = str(label)[:77] + "…"
        return f"{action}: {label}" + (f" ({role})" if role else "")
    if role:
        return f"{action}: {role}"
    return str(action)


def _browser_label(e: dict[str, Any]) -> str:
    meta = e.get("metadata") or {}
    action = str(meta.get("action") or e.get("event_type", "").replace("browser_", ""))
    page = meta.get("page") or {}
    target = meta.get("target") or {}
    host = str(page.get("hostname") or "")
    label = str(target.get("label") or target.get("name") or "")
    role = str(target.get("role") or target.get("tag") or "")
    if len(label) > 100:
        label = label[:97] + "…"
    action_text = action.replace("_", " ")
    if label:
        return f"{action_text}: {label}" + (f" ({role})" if role else "")
    if host:
        return f"{action_text}: {host}"
    return action_text




def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _friendly_browser_surface(hostname: str, pathname: str = "", title: str = "") -> str:
    host = (hostname or "").lower().strip(".")
    path = pathname or ""
    t = title or ""

    # High-value enterprise/browser tools. This is intentionally based on web
    # identity, not the browser process, so effort is attributed to the tool.
    if host in {"chatgpt.com", "chat.openai.com"}:
        return "ChatGPT"
    if host.endswith("lovable.dev"):
        return "Lovable"
    if host in {"github.com", "www.github.com"} or host.endswith(".github.com"):
        return "GitHub"
    if host in {"mail.google.com"}:
        return "Gmail"
    if host == "docs.google.com":
        if path.startswith("/spreadsheets"):
            return "Google Sheets"
        if path.startswith("/document"):
            return "Google Docs"
        if path.startswith("/presentation"):
            return "Google Slides"
        if path.startswith("/forms"):
            return "Google Forms"
        return "Google Workspace"
    if host in {"drive.google.com"}:
        return "Google Drive"
    if host.endswith("notion.so") or host == "notion.com" or host.endswith(".notion.com"):
        return "Notion"
    if host.endswith("slack.com"):
        return "Slack"
    if host in {"teams.microsoft.com", "teams.cloud.microsoft"}:
        return "Microsoft Teams"
    if host in {"outlook.office.com", "outlook.office365.com", "outlook.live.com"}:
        return "Outlook"
    if host.endswith("sharepoint.com"):
        return "SharePoint"
    if host in {"office.com", "www.office.com", "microsoft365.com", "www.microsoft365.com"}:
        return "Microsoft 365"
    if host.endswith("figma.com"):
        return "Figma"
    if host.endswith("linear.app"):
        return "Linear"
    if host.endswith("atlassian.net"):
        # Jira and Confluence often live on the same tenant domain; title helps.
        if "confluence" in t.lower():
            return "Confluence"
        if "jira" in t.lower():
            return "Jira"
        return "Atlassian"
    if host.endswith("salesforce.com") or host.endswith("force.com"):
        return "Salesforce"
    if host.endswith("hubspot.com"):
        return "HubSpot"

    if host.startswith("www."):
        host = host[4:]
    return host or _surface_from_title(t) or "Browser"


def _surface_from_title(title: str) -> str:
    t = (title or "").strip()
    low = t.lower()
    # Fallback for when the browser extension is absent or a semantic page
    # event was not available for the focus span.
    title_markers = [
        ("gmail", "Gmail"),
        ("google docs", "Google Docs"),
        ("google sheets", "Google Sheets"),
        ("google slides", "Google Slides"),
        ("google drive", "Google Drive"),
        ("chatgpt", "ChatGPT"),
        ("lovable", "Lovable"),
        ("github", "GitHub"),
        ("notion", "Notion"),
        ("salesforce", "Salesforce"),
        ("hubspot", "HubSpot"),
        ("figma", "Figma"),
        ("slack", "Slack"),
        ("microsoft teams", "Microsoft Teams"),
        ("outlook", "Outlook"),
        ("sharepoint", "SharePoint"),
    ]
    for marker, label in title_markers:
        if marker in low:
            return label
    # Keep the title as the last-resort surface rather than collapsing to Chrome.
    # Strip common browser suffixes and unread counters first.
    t = re.sub(r"^\(\d+\)\s*", "", t)
    t = re.sub(r"\s+[\-–—]\s+(Google Chrome|Chrome|Microsoft Edge|Edge|Firefox|Safari|Brave|Opera|Vivaldi)$", "", t, flags=re.I)
    return t[:90] if t else "Browser"


def _browser_context_by_session(events: list[dict[str, Any]]) -> dict[str, list[tuple[float, dict[str, Any]]]]:
    out: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for e in events:
        if not str(e.get("event_type") or "").startswith("browser_"):
            continue
        ts = _parse_ts(e.get("observed_at"))
        if ts is None:
            continue
        meta = e.get("metadata") or {}
        page = meta.get("page") or {}
        if page.get("hostname"):
            out[str(e.get("session_id") or "")].append((ts, page))
    for values in out.values():
        values.sort(key=lambda x: x[0])
    return out


def _effort_surface(e: dict[str, Any], browser_context: dict[str, list[tuple[float, dict[str, Any]]]]) -> tuple[str, str]:
    app = str(e.get("app") or "Unknown")
    if not is_browser_app(app):
        return app, app

    title = str(e.get("window_title") or "")
    start = _parse_ts(e.get("observed_at"))
    duration = float(e.get("duration_seconds") or 0)
    end = (start + duration) if start is not None else None
    candidates = browser_context.get(str(e.get("session_id") or ""), [])

    # Prefer a browser-extension page event that occurs during this focus span.
    # If several occur, the first one normally describes the page that just gained
    # focus; this avoids incorrectly assigning a subsequent fast navigation.
    page = None
    if start is not None and end is not None:
        inside = [(ts, p) for ts, p in candidates if start - 1.0 <= ts <= end + 0.75]
        if inside:
            # Prefer title similarity when available, otherwise earliest event.
            title_low = title.lower()
            matched = [x for x in inside if str(x[1].get("title") or "").lower() == title_low and title_low]
            page = (matched[0] if matched else inside[0])[1]
        else:
            prior = [(ts, p) for ts, p in candidates if ts <= start and start - ts <= 8.0]
            if prior:
                page = prior[-1][1]

    if page:
        surface = _friendly_browser_surface(
            str(page.get("hostname") or ""),
            str(page.get("pathname") or ""),
            str(page.get("title") or title),
        )
        return surface, app

    return _surface_from_title(title), app



_COMPLETION_RE = re.compile(
    r"\b(create|created|submit|submitted|send|sent|publish|published|approve|approved|complete|completed|finish|finished|merge|merged|deploy|deployed|invite|invited|upload|uploaded|confirm|confirmed)\b",
    re.I,
)

# Strong terminal actions are allowed to become task-boundary anchors. The wider
# completion regex above is still useful for labels, but words such as Upload,
# Invite or Confirm are often merely substeps inside a larger task.
_STRONG_COMPLETION_RE = re.compile(
    r"\b(send|sent|publish|published|complete|completed|finish|finished|merge|merged|deploy|deployed)\b|"
    r"\bcreate\s+(repository|repo|issue|pull request|project|ticket|record)\b|"
    r"\bsubmit\s+(new\s+)?(issue|ticket|request|application|form|review|response)\b|"
    r"\bapprove\s+(invoice|request|application|expense|payment|order|ticket|case)\b",
    re.I,
)


def _semantic_surface(e: dict[str, Any]) -> str:
    """Best-effort work-surface identity for a semantic event."""
    app = str(e.get("app") or "Unknown")
    meta = e.get("metadata") or {}
    if str(e.get("event_type") or "").startswith("browser_"):
        page = meta.get("page") or {}
        return _friendly_browser_surface(
            str(page.get("hostname") or ""),
            str(page.get("pathname") or ""),
            str(page.get("title") or e.get("window_title") or ""),
        )
    if is_browser_app(app):
        return _surface_from_title(str(e.get("window_title") or ""))
    return app


def _target_label(e: dict[str, Any]) -> str:
    meta = e.get("metadata") or {}
    target = meta.get("target") or {}
    for key in ("label", "title", "description", "name", "identifier"):
        value = str(target.get(key) or "").strip()
        if value:
            return value[:120]
    return ""


def _semantic_action(e: dict[str, Any]) -> str:
    meta = e.get("metadata") or {}
    return str(meta.get("action") or str(e.get("event_type") or "").replace("browser_", "").replace("screen_", ""))


def _is_completion_event(e: dict[str, Any]) -> bool:
    if not str(e.get("event_type") or "").startswith(("browser_", "screen_")):
        return False
    action = _semantic_action(e).lower()
    label = _target_label(e)
    if action in {"form_submit", "submit"}:
        return True
    return bool(label and _COMPLETION_RE.search(label))


def _completion_strength(e: dict[str, Any]) -> str:
    """Return strong/weak/none for task-boundary use.

    We keep weak completion-like controls as evidence and possible labels, but do
    not automatically split a task around them. This avoids fragmenting workflows
    around intermediate Upload/Confirm/Invite/Approve steps.
    """
    if not _is_completion_event(e):
        return "none"
    label = _target_label(e).strip()
    action = _semantic_action(e).lower()
    if label and _STRONG_COMPLETION_RE.search(label):
        return "strong"
    # A submit event with an explicit terminal-looking target can be strong. Bare
    # form_submit is weak because search/filter/login forms also submit.
    if action in {"submit", "form_submit"} and label and not re.search(r"\b(search|filter|sign in|log in|next|continue)\b", label, re.I):
        return "strong"
    return "weak"


def _semantic_fingerprint(e: dict[str, Any]) -> str:
    return "|".join([
        _semantic_surface(e).lower(),
        _semantic_action(e).lower().replace("_", " "),
        _normalize_task_anchor(_target_label(e)),
    ])


def _is_distinct_user_action_after_completion(e: dict[str, Any], pending_fingerprint: str, pending_ts: float, ts: float) -> bool:
    """Whether this event is evidence that the *next* task has started.

    Passive page/tab/navigation events are aftermath, not a new task. Browser and
    Accessibility sensors can also report the same physical click twice, so an
    equivalent event within 1.2 seconds is treated as a duplicate.
    """
    action = _semantic_action(e).lower()
    if action in {"page_view", "tab_activated", "navigation", "dom_ready", "pageshow", "heartbeat"}:
        return False
    if action in {"scroll", "mouse_move"}:
        return False
    fp = _semantic_fingerprint(e)
    if ts - pending_ts <= 1.2 and fp == pending_fingerprint:
        return False
    return action in {
        "click", "right_click", "focus_control", "control_change", "copy", "paste",
        "form_submit", "submit", "screen_click", "screen_right_click",
    } or str(e.get("event_type") or "").startswith("screen_")


def _canonical_task_label(events: list[dict[str, Any]], primary_surface: str) -> tuple[str, str, str, str]:
    """Human-facing task label plus stable family key and observed anchor.

    Labels describe the *inferred task execution*. They are intentionally more
    useful than raw UI text, while the observed UI anchor is retained separately so
    users can see what evidence the label came from.
    """
    semantic = [e for e in events if str(e.get("event_type") or "").startswith(("browser_", "screen_"))]
    completion = [e for e in semantic if _is_completion_event(e)]
    ce = completion[-1] if completion else None
    anchor = (_target_label(ce) or _semantic_action(ce).replace("_", " ")) if ce else ""
    labels = " | ".join(_target_label(e).lower() for e in semantic if _target_label(e))
    terminal_low = anchor.lower()
    surface_low = primary_surface.lower()

    # Email tasks need family identity to survive noisy browser capture. A fresh
    # compose can miss the initial Compose/New message control while still capturing
    # the terminal Send. Therefore any non-reply/non-forward email Send is treated
    # as the compose/send family rather than falling back to a separate email.send
    # family. This makes repeated-task discovery robust to one execution capturing
    # Compose and another execution missing it.
    semantic_surfaces = {_semantic_surface(e) for e in semantic}
    email_surface = primary_surface in {"Gmail", "Outlook"} or bool(semantic_surfaces & {"Gmail", "Outlook"})
    if email_surface and re.search(r"\bsend\b", terminal_low):
        if re.search(r"\breply\b", labels):
            return "Reply to email", "medium", "email.reply", anchor
        if re.search(r"\bforward\b", labels):
            return "Forward email", "medium", "email.forward", anchor
        # Fresh email is the safest default when Send is observed on an email
        # surface and no reply/forward evidence exists. Keep confidence medium and
        # retain the exact observed anchor for review.
        return "Compose and send email", "medium", "email.compose_send", anchor

    if primary_surface == "GitHub":
        if re.search(r"\bcreate repository\b", terminal_low):
            return "Create repository", "medium", "github.create_repository", anchor
        if re.search(r"\bsubmit new issue\b|\bcreate issue\b", terminal_low) or ("new issue" in labels and "submit" in terminal_low):
            return "Create issue", "medium", "github.create_issue", anchor
        if re.search(r"\bmerge\b", terminal_low):
            return "Merge pull request", "medium", "github.merge_pull_request", anchor

    if ce:
        normalized = _normalize_task_anchor(anchor)
        readable = anchor.strip() or _semantic_action(ce).replace("_", " ")
        return readable[:100], "medium", f"{surface_low}|{normalized or _semantic_action(ce).lower()}", anchor

    # Without a completion anchor, use the latest concrete control but keep the
    # confidence low. The family key remains conservative.
    for e in reversed(semantic):
        label = _target_label(e)
        if label:
            norm = _normalize_task_anchor(label)
            return label[:100], "low", f"{surface_low}|{norm}", label
    return f"Work in {primary_surface}", "low", f"{surface_low}|work", ""


def _normalize_task_anchor(text: str) -> str:
    """Normalize dynamic UI labels for repeated-task family matching."""
    value = (text or "").strip().lower()
    value = re.sub(r"^\(\d+\)\s*", "", value)
    value = re.sub(r"https?://\S+", "{url}", value)
    value = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{20,}\b", "{id}", value, flags=re.I)
    value = re.sub(r"\b[0-9a-f]{12,}\b", "{id}", value, flags=re.I)
    value = re.sub(r"#\s*\d{2,}\b", "#{n}", value)
    value = re.sub(r"\b\d{4,}\b", "{n}", value)
    value = re.sub(r"\s+", " ", value).strip(" -–—|·")
    return value[:120]


def _canonical_family_from_task(task: dict[str, Any]) -> str:
    """Recover a stable family from the whole inferred task evidence.

    This is intentionally a second line of defense after label inference. Real
    browser traces are lossy: one run may capture Compose, another only Send, and
    primary-surface attribution can occasionally fall back to a tab title. Repeated
    task discovery should still recognize both executions as the same family.
    """
    existing = str(task.get("task_family") or "").strip()
    actions = " | ".join(str(x) for x in (task.get("semantic_actions") or [])).lower()
    terminal = str(task.get("terminal_action") or "").lower()
    surfaces = {str(x).lower() for x in (task.get("surfaces") or [])}
    primary = str(task.get("primary_surface") or "").lower()
    evidence = " | ".join([actions, terminal, primary, " | ".join(sorted(surfaces))])

    email_evidence = (
        "gmail" in evidence
        or "outlook" in evidence
        or "mail.google.com" in evidence
        or "outlook.office" in evidence
        or "outlook.live" in evidence
    )
    if email_evidence and re.search(r"\bsend\b", evidence):
        if re.search(r"\breply\b", evidence):
            return "email.reply"
        if re.search(r"\bforward\b", evidence):
            return "email.forward"
        return "email.compose_send"

    return existing


def _task_action_token(e: dict[str, Any]) -> str:
    """Return a compact semantic milestone token for task-pattern matching.

    Navigation/page-view noise and generic unlabeled clicks are intentionally
    excluded. Repeated work is defined by what the user did, not by merely moving
    between sites.
    """
    if not str(e.get("event_type") or "").startswith(("browser_", "screen_")):
        return ""
    action = _semantic_action(e).lower()
    label = _target_label(e).strip().lower()

    # Known normalized labels retain business semantics without payload text.
    if label:
        token = re.sub(r"\s+", "_", _normalize_task_anchor(label))
        if token:
            return token

    if action == "focus_control":
        return "edit"
    if action == "control_change":
        return "change"
    if action in {"copy", "paste", "form_submit", "submit"}:
        return "submit" if action in {"form_submit", "submit"} else action
    # Site/tab movement is context, not task execution.
    if action in {
        "page_view", "tab_activated", "navigation", "navigation_started",
        "navigation_requested", "navigation_committed", "dom_ready", "pageshow",
        "heartbeat", "scroll", "mouse_move",
    }:
        return ""
    # A generic click with no safe label is too weak to define a repeated task.
    if action in {"click", "right_click", "screen_click", "screen_right_click"}:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", action).strip("_")[:60]


def _task_action_skeleton(events: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for e in events:
        token = _task_action_token(e)
        if token and (not out or out[-1] != token):
            out.append(token)
    return out[-16:]


def _task_signature(task: dict[str, Any]) -> str:
    """Stable family key for repeated candidate executions."""
    family = _canonical_family_from_task(task)
    if family:
        return family
    primary = str(task.get("primary_surface") or "Unknown")
    terminal = _normalize_task_anchor(str(task.get("terminal_action") or ""))
    if terminal:
        return f"{primary.lower()} | {terminal}"
    surfaces = task.get("surfaces") or []
    collapsed: list[str] = []
    for surface in surfaces:
        if not collapsed or collapsed[-1] != surface:
            collapsed.append(str(surface))
    semantic_actions = task.get("semantic_actions") or []
    anchor = _normalize_task_anchor(str(semantic_actions[-1] if semantic_actions else ""))
    raw = " > ".join(x.lower() for x in collapsed[:5])
    if anchor:
        raw += f" | {anchor}"
    return raw or primary.lower()


def _overlap_seconds(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    xs = sorted(float(v) for v in values)
    n = len(xs)
    mid = n // 2
    if n % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    pos = max(0.0, min(1.0, q)) * (len(xs) - 1)
    lo = int(pos)
    hi = min(len(xs) - 1, lo + 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac



def _build_task_session_index(session_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Index one session once so task intervals do not rescan the full session."""
    ordered: list[dict[str, Any]] = []
    event_times: list[float] = []
    focus_events: list[dict[str, Any]] = []
    focus_starts: list[float] = []
    for e in session_events:
        ts = _parse_ts(e.get("observed_at"))
        if ts is None:
            continue
        ordered.append(e)
        event_times.append(float(ts))
        if e.get("event_type") == "focus_span":
            focus_events.append(e)
            focus_starts.append(float(ts))
    return {
        "events": ordered,
        "event_times": event_times,
        "focus_events": focus_events,
        "focus_starts": focus_starts,
        "timed": list(zip(ordered, event_times)),
    }


def _task_from_interval(
    *,
    session_id: str,
    start_ts: float,
    end_ts: float,
    start_reason: str,
    end_reason: str,
    session_events: list[dict[str, Any]],
    browser_context: dict[str, list[tuple[float, dict[str, Any]]]],
    session_index: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build one candidate task and allocate focus effort by time overlap.

    When a task boundary lands inside a stored focus span, duration and aggregate
    input counts are apportioned by the overlap ratio. That is explicitly marked as
    an estimate so the system never presents sub-span key/click counts as exact.
    """
    if end_ts <= start_ts:
        return None

    index = session_index or _build_task_session_index(session_events)
    event_times = index["event_times"]
    event_items = index["events"]
    lo = bisect_left(event_times, start_ts - 0.001)
    hi = bisect_right(event_times, end_ts + 0.001)
    interval_events = event_items[lo:hi]
    semantic = [
        e for e in interval_events
        if str(e.get("event_type") or "").startswith(("browser_", "screen_"))
    ]

    # A focus span can start before a task boundary and overlap the interval.
    focus_events = index["focus_events"]
    focus_starts = index["focus_starts"]
    focus: list[dict[str, Any]] = []
    if focus_starts:
        start_i = max(0, bisect_right(focus_starts, start_ts) - 1)
        for pos in range(start_i, len(focus_events)):
            e = focus_events[pos]
            f0 = focus_starts[pos]
            if f0 > end_ts + 0.001:
                break
            f1 = f0 + max(0.0, float(e.get("duration_seconds") or 0))
            if f1 >= start_ts - 0.001:
                focus.append(e)

    surface_seconds: Counter[str] = Counter()
    surface_markers: list[tuple[float, str]] = []
    fg = engaged = idle = active_input = 0.0
    key_est = click_est = scroll_est = 0.0
    effort_estimated = False

    for e in focus:
        f0 = _parse_ts(e.get("observed_at"))
        if f0 is None:
            continue
        dur = max(0.0, float(e.get("duration_seconds") or 0))
        f1 = f0 + dur
        overlap = _overlap_seconds(start_ts, end_ts, f0, f1)
        if overlap <= 0:
            continue
        surface, _container = _effort_surface(e, browser_context)
        surface_seconds[surface] += overlap
        surface_markers.append((max(start_ts, f0), surface))
        ratio = overlap / dur if dur > 0 else 0.0
        if ratio < 0.999:
            effort_estimated = True
        activity = (e.get("metadata") or {}).get("activity") or {}
        fg += overlap
        engaged += float(activity.get("engaged_seconds") or 0) * ratio
        idle += float(activity.get("idle_seconds") or 0) * ratio
        active_input += float(activity.get("active_input_seconds") or 0) * ratio
        key_est += int(activity.get("keypress_count") or 0) * ratio
        click_est += int(activity.get("click_count") or 0) * ratio
        scroll_est += int(activity.get("scroll_count") or 0) * ratio

    for e in semantic:
        ts = _parse_ts(e.get("observed_at"))
        if ts is not None:
            surface_markers.append((ts, _semantic_surface(e)))

    surface_markers.sort(key=lambda x: x[0])
    surface_order: list[str] = []
    for _ts, surface in surface_markers:
        if surface and (not surface_order or surface_order[-1] != surface):
            surface_order.append(surface)

    if not focus and not semantic:
        return None
    if not semantic and fg < 1.0:
        return None

    primary = surface_seconds.most_common(1)[0][0] if surface_seconds else (_semantic_surface(semantic[-1]) if semantic else "Unknown")
    label, label_confidence, task_family, label_anchor = _canonical_task_label(interval_events, primary)

    semantic_labels: list[str] = []
    for e in semantic:
        text = _browser_label(e) if str(e.get("event_type") or "").startswith("browser_") else _interaction_label(e)
        if text and (not semantic_labels or semantic_labels[-1] != text):
            semantic_labels.append(text)

    completion = [e for e in semantic if _is_completion_event(e)]
    strong_completion = [e for e in semantic if _completion_strength(e) == "strong"]
    terminal = ""
    if completion:
        ce = completion[-1]
        terminal = _target_label(ce) or _semantic_action(ce).replace("_", " ")
    action_skeleton = _task_action_skeleton(interval_events)

    start_iso = datetime.fromtimestamp(start_ts, timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(end_ts, timezone.utc).isoformat()
    task_id = hashlib.sha256(f"{session_id}|{start_iso}|{end_iso}|{label}".encode()).hexdigest()[:16]

    if end_reason == "explicit_completion":
        boundary_confidence = "high"
    elif end_reason in {"inactivity_gap", "max_duration"}:
        boundary_confidence = "medium"
    else:
        boundary_confidence = "low"

    task = {
        "task_id": task_id,
        "session_id": session_id,
        "started_at": start_iso,
        "ended_at": end_iso,
        "elapsed_seconds": round(max(0.0, end_ts - start_ts), 3),
        "foreground_seconds": round(fg, 3),
        "engaged_seconds": round(engaged, 3),
        "idle_seconds": round(idle, 3),
        "active_input_seconds": round(active_input, 3),
        "keypress_count": int(round(key_est)),
        "click_count": int(round(click_est)),
        "scroll_count": int(round(scroll_est)),
        "effort_attribution": "overlap_proportional_estimate" if effort_estimated else "exact_focus_span",
        "effort_estimated": effort_estimated,
        "primary_surface": primary,
        "surfaces": surface_order,
        "semantic_action_count": len(semantic),
        "semantic_actions": semantic_labels[-24:],
        "terminal_action": terminal,
        "completion_observed": bool(strong_completion),
        "action_skeleton": action_skeleton,
        "label_evidence": label_anchor,
        "task_family": task_family,
        "suggested_label": label,
        "confidence": label_confidence,
        "boundary": {
            "start_reason": start_reason,
            "end_reason": end_reason,
            "confidence": boundary_confidence,
        },
        "inference": "heuristic_candidate_v3",
        "needs_review": True,
    }
    canonical_family = _canonical_family_from_task(task)
    if canonical_family:
        task["task_family"] = canonical_family
        if canonical_family == "email.compose_send" and task["suggested_label"] in {"Send email", "Send"}:
            task["suggested_label"] = "Compose and send email"
        elif canonical_family == "email.reply" and task["suggested_label"] in {"Send email", "Send"}:
            task["suggested_label"] = "Reply to email"
        elif canonical_family == "email.forward" and task["suggested_label"] in {"Send email", "Send"}:
            task["suggested_label"] = "Forward email"
    task["signature"] = _task_signature(task)
    return task


def candidate_tasks(
    limit: int = 25000,
    since: str | None = None,
    *,
    gap_seconds: float = 120.0,
    max_task_seconds: float = 1800.0,
    _raw_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Infer conservative candidate task executions from observed evidence.

    v3 task inference treats completion actions as anchors, then waits for a distinct new user action before splitting,
    long evidence gaps as medium-strength boundaries, and time-overlap allocation of
    focus/effort data. It can split two tasks that occur within one long browser focus
    span without assigning the entire focus span to both tasks.
    """
    raw_events = list(_raw_events) if _raw_events is not None else _event_rows(limit, since=since)
    events = [normalize_event(e) for e in raw_events]
    if not events:
        return {
            "tasks": [],
            "patterns": [],
            "inference": {
                "type": "heuristic_candidate_v3",
                "gap_seconds": gap_seconds,
                "max_task_seconds": max_task_seconds,
                "labels_are_suggestions": True,
                "subspan_effort_is_estimated": True,
            },
        }

    browser_context = _browser_context_by_session(events)
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if str(e.get("event_type") or "") == "heartbeat":
            continue
        by_session[str(e.get("session_id") or "")].append(e)

    tasks: list[dict[str, Any]] = []
    for session_id, session_events in by_session.items():
        session_events.sort(key=lambda e: _parse_ts(e.get("observed_at")) or 0)
        session_index = _build_task_session_index(session_events)
        timed = session_index["timed"]
        if not timed:
            continue

        current_start = float(timed[0][1])
        current_start_reason = "session_start"
        latest_coverage_end = current_start
        last_evidence_ts = current_start
        pending_completion_ts: float | None = None
        pending_completion_surface = ""
        pending_completion_fingerprint = ""
        pending_completion_strength = "none"

        intervals: list[tuple[float, float, str, str]] = []

        def close(end_ts: float, end_reason: str, next_start_reason: str) -> None:
            nonlocal current_start, current_start_reason, pending_completion_ts, pending_completion_surface, pending_completion_fingerprint, pending_completion_strength
            if end_ts > current_start + 0.001:
                intervals.append((current_start, end_ts, current_start_reason, end_reason))
            current_start = end_ts
            current_start_reason = next_start_reason
            pending_completion_ts = None
            pending_completion_surface = ""
            pending_completion_fingerprint = ""
            pending_completion_strength = "none"

        for idx, (e, ts_val) in enumerate(timed):
            ts = float(ts_val)
            et = str(e.get("event_type") or "")
            is_focus = et == "focus_span"
            is_semantic = et.startswith(("browser_", "screen_"))
            event_end = ts + (max(0.0, float(e.get("duration_seconds") or 0)) if is_focus else 0.0)

            if idx == 0:
                latest_coverage_end = max(latest_coverage_end, event_end)
                last_evidence_ts = ts
            else:
                gap_from = max(last_evidence_ts, min(latest_coverage_end, ts))
                actual_gap = max(0.0, ts - gap_from)

                # If another semantic action arrives before the current focus span
                # ends, a prior completion action is strong evidence that a new task
                # has begun on the same page/app. Split at the completion timestamp.
                if (
                    pending_completion_ts is not None
                    and pending_completion_strength == "strong"
                    and is_semantic
                    and ts > pending_completion_ts + 0.05
                    and _is_distinct_user_action_after_completion(
                        e, pending_completion_fingerprint, pending_completion_ts, ts
                    )
                ):
                    close(pending_completion_ts, "explicit_completion", "after_completion")
                    current_start = ts

                # If focus changes after a completion, keep the rest of the completed
                # focus span with the old task, then start the next task at the new
                # focus span. This avoids throwing away ordinary confirmation dwell.
                elif (
                    pending_completion_ts is not None
                    and pending_completion_strength == "strong"
                    and is_focus
                    and ts >= latest_coverage_end - 0.05
                ):
                    close(max(pending_completion_ts, min(ts, latest_coverage_end)), "explicit_completion", "after_completion")
                    current_start = ts

                elif actual_gap > gap_seconds:
                    end_at = max(current_start, min(ts, latest_coverage_end))
                    close(end_at, "inactivity_gap", "after_gap")
                    current_start = ts

                elif ts - current_start > max_task_seconds:
                    close(current_start + max_task_seconds, "max_duration", "after_max_duration")
                    current_start = ts

            latest_coverage_end = max(latest_coverage_end, event_end)
            last_evidence_ts = max(last_evidence_ts, ts)

            strength = _completion_strength(e)
            if strength != "none":
                pending_completion_ts = ts
                pending_completion_surface = _semantic_surface(e)
                pending_completion_fingerprint = _semantic_fingerprint(e)
                pending_completion_strength = strength

        session_end = max(latest_coverage_end, float(timed[-1][1]))
        if session_end > current_start + 0.001:
            intervals.append((current_start, session_end, current_start_reason, "session_end"))

        for start_ts, end_ts, start_reason, end_reason in intervals:
            task = _task_from_interval(
                session_id=session_id,
                start_ts=start_ts,
                end_ts=end_ts,
                start_reason=start_reason,
                end_reason=end_reason,
                session_events=session_events,
                browser_context=browser_context,
                session_index=session_index,
            )
            if task is not None:
                tasks.append(task)

    tasks.sort(key=lambda t: t["started_at"], reverse=True)

    # Repeated task families are intentionally TASK-based, not site-transition based.
    # Only executions with an observed strong completion anchor participate. Merely
    # bouncing between the same websites many times must never create a repeated task.
    pattern_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if not task.get("completion_observed"):
            continue
        signature = str(task.get("task_family") or "").strip()
        if not signature:
            skeleton = ">".join(task.get("action_skeleton") or [])
            surfaces = ">".join(str(x).lower() for x in (task.get("surfaces") or [])[:4])
            signature = f"{surfaces}|{skeleton}".strip("|")
        if signature:
            pattern_map[signature].append(task)

    patterns: list[dict[str, Any]] = []
    for signature, members in pattern_map.items():
        if len(members) < 2:
            continue
        engaged_values = [float(t["engaged_seconds"]) for t in members]
        elapsed_values = [float(t["elapsed_seconds"]) for t in members]
        key_values = [float(t["keypress_count"]) for t in members]
        common_surface_tuple = Counter(tuple(t["surfaces"]) for t in members).most_common(1)[0][0]
        boundary_end_counts = Counter((t.get("boundary") or {}).get("end_reason", "unknown") for t in members)
        patterns.append({
            "signature": signature,
            "task_family": Counter(str(t.get("task_family") or signature) for t in members).most_common(1)[0][0],
            "observed_count": len(members),
            "suggested_label": Counter(t["suggested_label"] for t in members).most_common(1)[0][0],
            "surfaces": list(common_surface_tuple),
            "action_skeleton": Counter(tuple(t.get("action_skeleton") or []) for t in members).most_common(1)[0][0],
            "surface_variant_count": len({tuple(t["surfaces"]) for t in members}),
            "repeat_basis": "completed_task_execution",
            "total_engaged_seconds": round(sum(engaged_values), 3),
            "median_engaged_seconds": round(_median(engaged_values), 3),
            "p90_engaged_seconds": round(_percentile(engaged_values, 0.9), 3),
            "median_elapsed_seconds": round(_median(elapsed_values), 3),
            "total_foreground_seconds": round(sum(float(t["foreground_seconds"]) for t in members), 3),
            "keypress_count": sum(int(t["keypress_count"]) for t in members),
            "median_keypress_count": round(_median(key_values), 1),
            "click_count": sum(int(t["click_count"]) for t in members),
            "completion_boundary_count": int(boundary_end_counts.get("explicit_completion", 0)),
            "effort_estimate_count": sum(1 for t in members if t.get("effort_estimated")),
            "confidence": "medium" if any(t["confidence"] == "medium" for t in members) else "low",
            "needs_review": True,
        })
    patterns.sort(key=lambda p: (p["observed_count"], p["total_engaged_seconds"]), reverse=True)

    return {
        "tasks": tasks[:250],
        "patterns": patterns[:80],
        "inference": {
            "type": "heuristic_candidate_v3",
            "gap_seconds": gap_seconds,
            "max_task_seconds": max_task_seconds,
            "labels_are_suggestions": True,
            "completion_actions_are_boundary_anchors": True,
            "repeated_patterns_require_observed_completion": True,
            "site_transitions_do_not_define_repeated_tasks": True,
            "passive_navigation_does_not_start_new_task": True,
            "duplicate_cross_sensor_actions_are_deduplicated": True,
            "subspan_effort_is_estimated": True,
        },
    }

def semantic_activity(limit: int = 200, since: str | None = None, *, operational: bool = False) -> list[dict[str, Any]]:
    loader = _operational_event_rows if operational else _event_rows
    events = loader(max(limit * 6, 500), since=since)
    semantic: list[dict[str, Any]] = []
    for e in events:
        et = str(e.get("event_type") or "")
        if et.startswith("browser_"):
            meta = e.get("metadata") or {}
            semantic.append({
                "observed_at": e.get("observed_at"),
                "session_id": e.get("session_id"),
                "app": e.get("app"),
                "window_title": e.get("window_title"),
                "event_type": et,
                "label": _browser_label(e),
                "page": meta.get("page") or {},
                "target": meta.get("target") or {},
                "source": "browser_extension",
            })
        elif et.startswith("screen_"):
            meta = e.get("metadata") or {}
            semantic.append({
                "observed_at": e.get("observed_at"),
                "session_id": e.get("session_id"),
                "app": e.get("app"),
                "window_title": e.get("window_title"),
                "event_type": et,
                "label": _interaction_label(e),
                "page": {},
                "target": meta.get("target") or {},
                "source": "desktop_accessibility",
            })
    return semantic[-max(1, min(limit, 2000)):][::-1]


def _summary_uncached(limit: int = 10000, since: str | None = None, *, operational: bool = False) -> dict[str, Any]:
    events = _operational_event_rows(limit, since=since) if operational else _event_rows(limit, since=since)
    focus_events = [e for e in events if e.get("event_type", "focus_span") == "focus_span"]
    interaction_events = [e for e in events if str(e.get("event_type", "")).startswith("screen_")]
    browser_events = [e for e in events if str(e.get("event_type", "")).startswith("browser_")]
    if not events:
        return {
            "events": 0,
            "focus_events": 0,
            "screen_interactions": 0,
            "browser_semantic_events": 0,
            "sessions": 0,
            "apps": [],
            "surfaces": [],
            "transitions": [],
            "frequent_sequences": [],
            "recent_interactions": [],
            "recent_browser_actions": [],
            "recent_evidence": [],
            "semantic_action_counts": [],
            "candidate_task_count": 0,
            "repeated_task_pattern_count": 0,
            "candidate_tasks": [],
            "repeated_task_patterns": [],
            "total_active_seconds": 0.0,
            "total_engaged_seconds": 0.0,
            "total_idle_seconds": 0.0,
            "total_active_input_seconds": 0.0,
            "keypress_count": 0,
            "click_count": 0,
            "scroll_count": 0,
            "data_layer": "operational_normalized" if operational else "raw_local_evidence",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    apps = Counter()
    app_seconds = Counter()
    app_engaged = Counter()
    app_idle = Counter()
    app_active_input = Counter()
    app_keys = Counter()
    app_clicks = Counter()
    app_scrolls = Counter()

    surfaces = Counter()
    surface_seconds = Counter()
    surface_engaged = Counter()
    surface_idle = Counter()
    surface_active_input = Counter()
    surface_keys = Counter()
    surface_clicks = Counter()
    surface_scrolls = Counter()
    surface_containers: dict[str, Counter] = defaultdict(Counter)
    browser_context = _browser_context_by_session(events)
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        sessions[e["session_id"]].append(e)
    for e in focus_events:
        if e.get("app"):
            app = e["app"]
            apps[app] += 1
            app_seconds[app] += float(e.get("duration_seconds") or 0)
            activity = (e.get("metadata") or {}).get("activity") or {}
            app_engaged[app] += float(activity.get("engaged_seconds") or 0)
            app_idle[app] += float(activity.get("idle_seconds") or 0)
            app_active_input[app] += float(activity.get("active_input_seconds") or 0)
            app_keys[app] += int(activity.get("keypress_count") or 0)
            app_clicks[app] += int(activity.get("click_count") or 0)
            app_scrolls[app] += int(activity.get("scroll_count") or 0)

            surface, container = _effort_surface(e, browser_context)
            surfaces[surface] += 1
            surface_seconds[surface] += float(e.get("duration_seconds") or 0)
            surface_engaged[surface] += float(activity.get("engaged_seconds") or 0)
            surface_idle[surface] += float(activity.get("idle_seconds") or 0)
            surface_active_input[surface] += float(activity.get("active_input_seconds") or 0)
            surface_keys[surface] += int(activity.get("keypress_count") or 0)
            surface_clicks[surface] += int(activity.get("click_count") or 0)
            surface_scrolls[surface] += int(activity.get("scroll_count") or 0)
            surface_containers[surface][container] += 1

    transitions = Counter()
    sequences = Counter()
    focus_sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in focus_events:
        focus_sessions[e["session_id"]].append(e)
    for session_events in focus_sessions.values():
        collapsed: list[str] = []
        for e in session_events:
            step = _workflow_label(e)
            if not collapsed or collapsed[-1] != step:
                collapsed.append(step)
        for a, b in zip(collapsed, collapsed[1:]):
            transitions[(a, b)] += 1
        for n in (3, 4, 5):
            for i in range(0, max(0, len(collapsed) - n + 1)):
                sequences[tuple(collapsed[i:i+n])] += 1

    first = events[0]["observed_at"]
    last = events[-1]["observed_at"]
    recent_interactions = []
    for e in interaction_events[-30:][::-1]:
        meta = e.get("metadata") or {}
        recent_interactions.append({
            "observed_at": e.get("observed_at"),
            "app": e.get("app"),
            "window_title": e.get("window_title"),
            "action": meta.get("action") or str(e.get("event_type", "")).replace("screen_", ""),
            "label": _interaction_label(e),
            "target": meta.get("target") or {},
        })

    recent_browser_actions = []
    semantic_counts = Counter()
    for e in browser_events:
        meta = e.get("metadata") or {}
        semantic_counts[str(meta.get("action") or e.get("event_type", "")).replace("browser_", "")] += 1
    for e in browser_events[-40:][::-1]:
        meta = e.get("metadata") or {}
        page = meta.get("page") or {}
        recent_browser_actions.append({
            "observed_at": e.get("observed_at"),
            "app": e.get("app"),
            "window_title": e.get("window_title"),
            "action": meta.get("action") or str(e.get("event_type", "")).replace("browser_", ""),
            "label": _browser_label(e),
            "hostname": page.get("hostname"),
            "pathname": page.get("pathname"),
            "target": meta.get("target") or {},
        })

    # Unified local evidence feed: browser navigation/actions and desktop
    # accessibility interactions in one chronological view. This makes a brief
    # address-bar visit visible even when the user never scrolls or clicks.
    recent_evidence: list[dict[str, Any]] = []
    for e in interaction_events[-60:]:
        meta = e.get("metadata") or {}
        recent_evidence.append({
            "observed_at": e.get("observed_at"),
            "source": "desktop",
            "app": e.get("app"),
            "window_title": e.get("window_title"),
            "hostname": "",
            "pathname": "",
            "action": meta.get("action") or str(e.get("event_type", "")).replace("screen_", ""),
            "label": _interaction_label(e),
        })
    for e in browser_events[-80:]:
        meta = e.get("metadata") or {}
        page = meta.get("page") or {}
        recent_evidence.append({
            "observed_at": e.get("observed_at"),
            "source": "browser",
            "app": e.get("app"),
            "window_title": e.get("window_title"),
            "hostname": page.get("hostname") or "",
            "pathname": page.get("pathname") or "",
            "action": meta.get("action") or str(e.get("event_type", "")).replace("browser_", ""),
            "label": _browser_label(e),
        })
    recent_evidence.sort(key=lambda x: _parse_ts(x.get("observed_at")) or 0, reverse=True)
    # Hide near-duplicate navigation API echoes from the dashboard while keeping
    # them in raw storage. One visible row per destination/action is enough.
    deduped_evidence: list[dict[str, Any]] = []
    seen_nav: dict[tuple[str, str], float] = {}
    for item in recent_evidence:
        action = str(item.get("action") or "")
        ts = _parse_ts(item.get("observed_at")) or 0
        if item.get("source") == "browser" and action.startswith("navigation"):
            key = (str(item.get("hostname") or ""), str(item.get("pathname") or ""))
            prior = seen_nav.get(key)
            if prior is not None and abs(prior - ts) < 2.0:
                continue
            seen_nav[key] = ts
        deduped_evidence.append(item)
        if len(deduped_evidence) >= 50:
            break

    task_data = candidate_tasks(
        limit=limit,
        since=since,
        _raw_events=events if not operational else None,
    )

    return {
        "events": len(events),
        "focus_events": len(focus_events),
        "screen_interactions": len(interaction_events),
        "browser_semantic_events": len(browser_events),
        "sessions": len(sessions),
        "first_observed_at": first,
        "last_observed_at": last,
        "total_active_seconds": round(sum(app_seconds.values()), 3),
        "total_engaged_seconds": round(sum(app_engaged.values()), 3),
        "total_idle_seconds": round(sum(app_idle.values()), 3),
        "total_active_input_seconds": round(sum(app_active_input.values()), 3),
        "keypress_count": int(sum(app_keys.values())),
        "click_count": int(sum(app_clicks.values())),
        "scroll_count": int(sum(app_scrolls.values())),
        "surfaces": [
            {
                "surface": name,
                "container_app": surface_containers[name].most_common(1)[0][0] if surface_containers[name] else name,
                "events": c,
                "active_seconds": round(surface_seconds[name], 3),
                "engaged_seconds": round(surface_engaged[name], 3),
                "idle_seconds": round(surface_idle[name], 3),
                "active_input_seconds": round(surface_active_input[name], 3),
                "keypress_count": int(surface_keys[name]),
                "click_count": int(surface_clicks[name]),
                "scroll_count": int(surface_scrolls[name]),
            }
            for name, c in surfaces.most_common(30)
        ],
        "apps": [
            {
                "app": a, "events": c,
                "active_seconds": round(app_seconds[a], 3),
                "engaged_seconds": round(app_engaged[a], 3),
                "idle_seconds": round(app_idle[a], 3),
                "active_input_seconds": round(app_active_input[a], 3),
                "keypress_count": int(app_keys[a]),
                "click_count": int(app_clicks[a]),
                "scroll_count": int(app_scrolls[a]),
            }
            for a, c in apps.most_common(15)
        ],
        "transitions": [
            {"from": a, "to": b, "count": c}
            for (a, b), c in transitions.most_common(20)
        ],
        "frequent_sequences": [
            {"sequence": list(seq), "count": c}
            for seq, c in sequences.most_common(20)
            if c >= 2
        ],
        "recent_interactions": recent_interactions,
        "recent_browser_actions": recent_browser_actions,
        "recent_evidence": deduped_evidence,
        "semantic_action_counts": [
            {"action": action, "count": count}
            for action, count in semantic_counts.most_common(20)
        ],
        "candidate_task_count": len(task_data.get("tasks", [])),
        "repeated_task_pattern_count": len(task_data.get("patterns", [])),
        "candidate_tasks": task_data.get("tasks", [])[:25],
        "repeated_task_patterns": task_data.get("patterns", [])[:20],
        "task_inference": task_data.get("inference", {}),
        "data_layer": "operational_normalized" if operational else "raw_local_evidence",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def search_events(query: str = "", app: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if query:
        clauses.append("(LOWER(window_title) LIKE ? OR LOWER(app) LIKE ? OR LOWER(metadata_json) LIKE ?)")
        q = f"%{query.lower()}%"
        params.extend([q, q, q])
    if app:
        clauses.append("LOWER(app) = ?")
        params.append(app.lower())
    params.append(max(1, min(limit, 1000)))
    return rows(
        f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY observed_at DESC LIMIT ?",
        tuple(params),
    )


def search_operational_events(query: str = "", surface: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if query:
        clauses.append("(LOWER(window_title) LIKE ? OR LOWER(app) LIKE ? OR LOWER(metadata_json) LIKE ?)")
        q = f"%{query.lower()}%"
        params.extend([q, q, q])
    if surface:
        clauses.append("LOWER(app) = ?")
        params.append(surface.lower())
    params.append(max(1, min(limit, 1000)))
    return normalized_rows(
        f"SELECT * FROM normalized_events WHERE {' AND '.join(clauses)} ORDER BY observed_at DESC LIMIT ?",
        tuple(params),
    )


def operational_timeline(session_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    return normalized_rows(
        "SELECT * FROM normalized_events WHERE session_id = ? ORDER BY observed_at ASC LIMIT ?",
        (session_id, max(1, min(limit, 5000))),
    )


def timeline(session_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    return rows(
        "SELECT * FROM events WHERE session_id = ? ORDER BY observed_at ASC LIMIT ?",
        (session_id, max(1, min(limit, 5000))),
    )


_SUMMARY_CACHE_LOCK = threading.RLock()
_SUMMARY_CACHE: dict[tuple[Any, ...], tuple[tuple[int, int], dict[str, Any]]] = {}


def clear_summary_cache() -> None:
    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE.clear()


def summary(limit: int = 10000, since: str | None = None, *, operational: bool = False) -> dict[str, Any]:
    """Cached summary keyed by append-only DB revision.

    The returned value is deep-copied because API code adds live collector/browser
    state after analytics.  Late queued events change MAX(id)/COUNT and therefore
    invalidate the cache even when their observed_at timestamp is old.
    """
    bounded = max(1, min(int(limit), 100000))
    revision = table_revision(operational=operational)
    key = (bounded, since or "", bool(operational))
    cacheable = revision[0] >= 0
    if cacheable:
        with _SUMMARY_CACHE_LOCK:
            cached = _SUMMARY_CACHE.get(key)
            if cached and cached[0] == revision:
                return copy.deepcopy(cached[1])

    result = _summary_uncached(bounded, since=since, operational=operational)

    # Browser evidence enrichment is explicit, not installed by package import.
    from . import browser_context_policy
    result = browser_context_policy.enrich_summary(
        result,
        __import__(__name__, fromlist=["_"]),
        since=since,
    )

    if cacheable:
        with _SUMMARY_CACHE_LOCK:
            _SUMMARY_CACHE[key] = (revision, copy.deepcopy(result))
            # Keep only a handful of active scope/limit combinations.
            if len(_SUMMARY_CACHE) > 8:
                oldest = next(iter(_SUMMARY_CACHE))
                if oldest != key:
                    _SUMMARY_CACHE.pop(oldest, None)
    return copy.deepcopy(result)
