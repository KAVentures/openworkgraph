from __future__ import annotations

"""Presentation/context enrichment for desktop browser evidence.

The desktop accessibility sensor knows that Chrome/Edge/etc. is focused and often
has only a page title. The browser semantic sensor independently knows the real
hostname. This policy joins those summary representations so a desktop interaction
on ``chatgpt.com`` is shown as ChatGPT even when the page title itself does not
contain the product name.

v0.42 treats browser identity as short-lived *session state* rather than requiring
another semantic browser event every few seconds. This is display/summary-only:
raw events, task inference, timing, and stored evidence are not rewritten.
"""

import re
from typing import Any


# A browser tab can legitimately remain active without emitting semantic browser
# actions for many minutes (reading/thinking/typing captured by the desktop sensor).
# Keep same-session state long enough for that normal dwell, but never indefinitely.
ACTIVE_CONTEXT_MAX_AGE_SECONDS = 30 * 60
LEGACY_CONTEXT_MAX_AGE_SECONDS = 8.0
FUTURE_JITTER_SECONDS = 1.0
SAME_ACTION_WINDOW_SECONDS = 2.5


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


def install(analytics: Any) -> None:
    """Wrap ``analytics.summary`` with conservative active-browser attribution."""
    previous = analytics.summary
    if getattr(previous, "_openworkgraph_browser_context_policy", False):
        return

    def summary(limit: int = 10000, since: str | None = None, *, operational: bool = False) -> dict[str, Any]:
        result = previous(limit=limit, since=since, operational=operational)
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

            # Prefer candidates from the same work session. Old summary rows from
            # before v0.42 do not have session_id, so they fall back to the legacy
            # narrow time window instead of receiving long-lived attribution.
            if session_id:
                pool = [candidate for candidate in candidates if candidate["session_id"] == session_id]
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
                max_age = ACTIVE_CONTEXT_MAX_AGE_SECONDS if session_id and candidate["session_id"] else LEGACY_CONTEXT_MAX_AGE_SECONDS
                if age <= max_age:
                    return candidate, age, "active_session" if max_age == ACTIVE_CONTEXT_MAX_AGE_SECONDS else "legacy_recent"

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

            # Explicit semantic fields are useful to API/export consumers and let
            # the dashboard distinguish work surface, page, browser container and
            # sensor provenance without conflating them.
            item["work_surface"] = surface
            item["container_app"] = container_app
            item["browser_hostname"] = hostname
            item["browser_pathname"] = pathname
            item["page_title"] = browser_title
            item["browser_context_join"] = join_reason
            if context_age is not None:
                item["browser_context_age_seconds"] = round(float(context_age), 3)

            # Backward-compatible fields for existing dashboard/API consumers.
            item["app"] = surface
            item["hostname"] = ""
            item["pathname"] = ""
            detail_parts: list[str] = []
            if browser_title and browser_title.casefold() != surface.casefold():
                detail_parts.append(browser_title)
            detail_parts.append(hostname)
            detail_parts.append(container_app)
            item["window_title"] = " · ".join(part for part in detail_parts if part)

        return result

    summary._openworkgraph_browser_context_policy = True  # type: ignore[attr-defined]
    analytics.summary = summary
