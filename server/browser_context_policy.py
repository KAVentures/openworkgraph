from __future__ import annotations

"""Presentation/context enrichment for desktop browser evidence.

The desktop accessibility sensor knows that Chrome/Edge/etc. is focused and often
has only a page title. The browser semantic sensor independently knows the real
hostname. This policy joins those summary representations so a desktop interaction
on ``chatgpt.com`` can be labeled with the ChatGPT work surface even when the page
title itself does not contain the product name.

v0.42 treats browser identity as short-lived *active context* rather than requiring
another semantic browser event every few seconds. v0.42.2 fixes a live-summary gap:
base summary rows do not currently retain session ids, so long-lived attribution
must also be able to use the latest browser semantic event in the current run. A
known conflicting site/tool title still blocks carry-forward. This is display/
summary-only: raw events, task inference, timing, and stored evidence are not
rewritten.
"""

import re
from typing import Any


ACTIVE_CONTEXT_MAX_AGE_SECONDS = 30 * 60
LEGACY_CONTEXT_MAX_AGE_SECONDS = 8.0
FUTURE_JITTER_SECONDS = 1.0
SAME_ACTION_WINDOW_SECONDS = 2.5

# These are only conflict guards for stale browser context. They are not used to
# infer browser identity when a hostname is available.
_TITLE_SURFACE_HINTS = (
    ("chatgpt", "ChatGPT"),
    ("supabase", "Supabase"),
    ("gmail", "Gmail"),
    ("google docs", "Google Docs"),
    ("google sheets", "Google Sheets"),
    ("google slides", "Google Slides"),
    ("google drive", "Google Drive"),
    ("github", "GitHub"),
    ("notion", "Notion"),
    ("salesforce", "Salesforce"),
    ("hubspot", "HubSpot"),
    ("figma", "Figma"),
    ("slack", "Slack"),
    ("microsoft teams", "Microsoft Teams"),
    ("outlook", "Outlook"),
    ("sharepoint", "SharePoint"),
    ("lovable", "Lovable"),
    ("vercel", "Vercel"),
)


def _action_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ").strip().casefold())


def _clean_page_title(value: str) -> str:
    title = str(value or "").strip()
    title = re.sub(
        r"\s+[\-–—]\s+(Google Chrome|Chrome|Microsoft Edge|Edge|Firefox|Safari|Brave|Opera|Vivaldi)(?:\s+[\-–—].*)?$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    return title


def _titles_match(desktop_title: str, browser_title: str) -> bool:
    desktop = re.sub(r"\s+", " ", _clean_page_title(desktop_title)).strip().casefold()
    browser = re.sub(r"\s+", " ", _clean_page_title(browser_title)).strip().casefold()
    if not desktop or not browser:
        return False
    if desktop == browser:
        return True
    # Accessibility APIs occasionally append profile/tenant text. Require a
    # substantial shared title rather than accepting arbitrary short substrings.
    shorter, longer = sorted((desktop, browser), key=len)
    return len(shorter) >= 12 and shorter in longer


def _known_title_surface(title: str) -> str:
    low = _clean_page_title(title).casefold()
    for marker, surface in _TITLE_SURFACE_HINTS:
        if marker in low:
            return surface
    return ""


def _title_conflicts_with_candidate(desktop_title: str, candidate_surface: str) -> bool:
    """Whether the desktop title positively identifies a different known tool."""
    hinted = _known_title_surface(desktop_title)
    return bool(hinted and hinted.casefold() != candidate_surface.casefold())


def enrich_summary(
    result: dict[str, Any],
    analytics: Any,
    *,
    since: str | None = None,
) -> dict[str, Any]:
    """Conservatively attach browser work-surface context to summary evidence."""
    evidence = result.get("recent_evidence")
    if not isinstance(evidence, list) or not evidence:
        return result

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def add_candidate(item: Any) -> None:
        if not isinstance(item, dict):
            return
        host = str(item.get("hostname") or "").strip()
        ts = analytics._parse_ts(item.get("observed_at"))
        if not host or ts is None:
            return
        session_id = str(item.get("session_id") or "")
        key = (
            session_id,
            str(item.get("observed_at") or ""),
            host,
            str(item.get("pathname") or ""),
            _action_key(item.get("action")),
        )
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "ts": float(ts),
            "session_id": session_id,
            "hostname": host,
            "pathname": str(item.get("pathname") or ""),
            "window_title": str(item.get("window_title") or ""),
            "action": _action_key(item.get("action")),
        })

    for item in evidence:
        if isinstance(item, dict) and item.get("source") == "browser":
            add_candidate(item)
    for item in result.get("recent_browser_actions") or []:
        add_candidate(item)

    if not candidates:
        return result
    candidates.sort(key=lambda item: item["ts"])

    def best_candidate(item: dict[str, Any]) -> tuple[dict[str, Any] | None, float | None, str]:
        ts = analytics._parse_ts(item.get("observed_at"))
        if ts is None:
            return None, None, "none"
        ts = float(ts)
        action = _action_key(item.get("action"))
        session_id = str(item.get("session_id") or "")

        if session_id:
            same_session = [candidate for candidate in candidates if candidate["session_id"] == session_id]
            pool = same_session or candidates
        else:
            pool = candidates
        if not pool:
            return None, None, "none"

        same_action = [
            candidate
            for candidate in pool
            if candidate["action"] == action and abs(candidate["ts"] - ts) <= SAME_ACTION_WINDOW_SECONDS
        ]
        if same_action:
            candidate = min(same_action, key=lambda candidate: abs(candidate["ts"] - ts))
            return candidate, abs(candidate["ts"] - ts), "same_action"

        prior = [candidate for candidate in pool if candidate["ts"] <= ts]
        if prior:
            candidate = max(prior, key=lambda candidate: candidate["ts"])
            age = ts - candidate["ts"]
            explicit_same_session = bool(
                session_id
                and candidate["session_id"]
                and candidate["session_id"] == session_id
            )
            title_match = _titles_match(
                str(item.get("window_title") or ""),
                candidate["window_title"],
            )
            candidate_surface = analytics._friendly_browser_surface(
                candidate["hostname"], candidate["pathname"], candidate["window_title"]
            )
            title_conflict = _title_conflicts_with_candidate(
                str(item.get("window_title") or ""), candidate_surface
            )

            # Current-run summaries are the live dashboard. The latest browser
            # semantic event is the active browser state until the extension
            # reports a new tab/navigation. This fixes long dwell on ChatGPT
            # where the desktop title never says "ChatGPT". A desktop title
            # that positively names another known tool blocks stale carry.
            if (
                since is not None
                and age <= ACTIVE_CONTEXT_MAX_AGE_SECONDS
                and not title_conflict
            ):
                if explicit_same_session:
                    return candidate, age, "active_session"
                if title_match:
                    return candidate, age, "active_run_title_match"
                return candidate, age, "active_run_latest_browser"

            # Outside the current live run, only carry browser identity when
            # there is explicit session evidence or the event is very recent.
            if (
                explicit_same_session
                and age <= ACTIVE_CONTEXT_MAX_AGE_SECONDS
                and not title_conflict
            ):
                return candidate, age, "active_session"
            if age <= LEGACY_CONTEXT_MAX_AGE_SECONDS:
                return candidate, age, "legacy_recent"

        future = [
            candidate for candidate in pool
            if 0 <= candidate["ts"] - ts <= FUTURE_JITTER_SECONDS
        ]
        if future:
            candidate = min(future, key=lambda candidate: candidate["ts"])
            return candidate, candidate["ts"] - ts, "future_jitter"
        return None, None, "none"

    for item in evidence:
        if not isinstance(item, dict) or item.get("source") != "desktop":
            continue
        container_app = str(item.get("app") or "")
        if not analytics.is_browser_app(container_app):
            continue
        candidate, context_age, join_reason = best_candidate(item)
        if not candidate:
            continue

        hostname = candidate["hostname"]
        pathname = candidate["pathname"]
        browser_title = _clean_page_title(candidate["window_title"])
        surface = analytics._friendly_browser_surface(hostname, pathname, browser_title)
        if not surface or surface == "Browser":
            continue

        item["work_surface"] = surface
        item["container_app"] = container_app
        item["browser_hostname"] = hostname
        item["browser_pathname"] = pathname
        item["page_title"] = browser_title
        item["browser_context_join"] = join_reason
        if context_age is not None:
            item["browser_context_age_seconds"] = round(float(context_age), 3)

        # Keep the actual container app visible. The dashboard uses
        # `work_surface` as the small suffix label (for example · ChatGPT),
        # while `source` continues to describe the observing sensor.
        item["app"] = container_app
        item["hostname"] = ""
        item["pathname"] = ""
        detail_parts: list[str] = []
        if browser_title and browser_title.casefold() != surface.casefold():
            detail_parts.append(browser_title)
        detail_parts.append(hostname)
        detail_parts.append(container_app)
        item["window_title"] = " · ".join(part for part in detail_parts if part)

    return result

    return result


def install(analytics: Any) -> None:
    """Backward-compatible installer; production calls enrich_summary explicitly."""
    previous = analytics.summary
    if getattr(previous, "_openworkgraph_browser_context_policy", False):
        return

    def summary(limit: int = 10000, since: str | None = None, *, operational: bool = False) -> dict[str, Any]:
        return enrich_summary(
            previous(limit=limit, since=since, operational=operational),
            analytics,
            since=since,
        )

    summary._openworkgraph_browser_context_policy = True  # type: ignore[attr-defined]
    analytics.summary = summary
