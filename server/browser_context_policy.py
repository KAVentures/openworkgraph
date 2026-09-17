from __future__ import annotations

"""Presentation/context enrichment for desktop browser evidence.

The desktop accessibility sensor knows that Google Chrome is focused and often has
only a page title.  The browser semantic sensor independently knows the real URL
and hostname.  This policy joins those two *summary representations* by timestamp
so a Chrome desktop click on ``chatgpt.com`` can be shown as ChatGPT even when the
page title itself does not contain the word "ChatGPT".

Raw events, stored window titles, task inference and effort accounting are not
rewritten.
"""

import re
from typing import Any


def _action_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ").strip().casefold())


def _clean_page_title(value: str) -> str:
    title = str(value or "").strip()
    # Browser-extension events already carry document.title, but keep a fallback
    # cleaner for older events or desktop titles that include the browser suffix.
    title = re.sub(
        r"\s+[\-–—]\s+(Google Chrome|Chrome|Microsoft Edge|Edge|Firefox|Safari|Brave|Opera|Vivaldi)(?:\s+[\-–—].*)?$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    return title


def install(analytics: Any) -> None:
    """Wrap ``analytics.summary`` with conservative browser-context joining."""
    previous = analytics.summary
    if getattr(previous, "_openworkgraph_browser_context_policy", False):
        return

    def summary(limit: int = 10000, since: str | None = None, *, operational: bool = False) -> dict[str, Any]:
        result = previous(limit=limit, since=since, operational=operational)
        evidence = result.get("recent_evidence")
        if not isinstance(evidence, list) or not evidence:
            return result

        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()

        def add_candidate(item: Any) -> None:
            if not isinstance(item, dict):
                return
            host = str(item.get("hostname") or "").strip()
            ts = analytics._parse_ts(item.get("observed_at"))
            if not host or ts is None:
                return
            key = (
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

        def best_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
            ts = analytics._parse_ts(item.get("observed_at"))
            if ts is None:
                return None
            ts = float(ts)
            action = _action_key(item.get("action"))

            # Cross-sensor reports of the same click/change are normally nearly
            # simultaneous and are the strongest possible join signal.
            same_action = [
                candidate
                for candidate in candidates
                if candidate["action"] == action and abs(candidate["ts"] - ts) <= 2.5
            ]
            if same_action:
                return min(same_action, key=lambda candidate: abs(candidate["ts"] - ts))

            # Otherwise use recent active browser context, mirroring the existing
            # effort-surface logic.  A short future allowance handles sensor-order
            # jitter without letting a later navigation relabel an old desktop row.
            prior = [candidate for candidate in candidates if 0 <= ts - candidate["ts"] <= 8.0]
            if prior:
                return max(prior, key=lambda candidate: candidate["ts"])
            future = [candidate for candidate in candidates if 0 <= candidate["ts"] - ts <= 1.0]
            if future:
                return min(future, key=lambda candidate: candidate["ts"])
            return None

        for item in evidence:
            if not isinstance(item, dict) or item.get("source") != "desktop":
                continue
            container_app = str(item.get("app") or "")
            if not analytics.is_browser_app(container_app):
                continue
            candidate = best_candidate(item)
            if not candidate:
                continue

            hostname = candidate["hostname"]
            pathname = candidate["pathname"]
            browser_title = _clean_page_title(candidate["window_title"])
            surface = analytics._friendly_browser_surface(hostname, pathname, browser_title)
            if not surface or surface == "Browser":
                continue

            # Add explicit semantic fields for API/export consumers.
            item["work_surface"] = surface
            item["container_app"] = container_app
            item["browser_hostname"] = hostname
            item["browser_pathname"] = pathname
            item["page_title"] = browser_title

            # Backward-compatible display fields for the current dashboard.  The
            # first line becomes the actual tool (ChatGPT, Gmail, Supabase, ...),
            # while the smaller second line retains page title + hostname + browser.
            # These are summary-only copies; the event database is untouched.
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
