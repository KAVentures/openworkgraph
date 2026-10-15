"""Privacy-minimized organizational workflow insights from *synced* evidence only.

No endpoint-local read or unsynced history. No personnel ranking, employee IDs,
window titles, free text, raw metadata, file names, sessions, or event references
are returned. Small groups are suppressed to reduce reidentification risk.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re
from typing import Any

from .workflow_evidence import load_gateway_evidence

# Trusted categories, not app names taken from untrusted window titles.
_APP_NAMES = {
    "gmail": "Gmail", "google mail": "Gmail", "outlook": "Outlook",
    "microsoft outlook": "Outlook", "excel": "Excel",
    "microsoft excel": "Excel", "fortnox": "Fortnox",
    "github": "GitHub", "jira": "Jira", "linear": "Linear",
    "slack": "Slack", "microsoft teams": "Teams",
    "google sheets": "Sheets", "google docs": "Docs",
    "google drive": "Drive", "salesforce": "Salesforce",
    "notion": "Notion", "figma": "Figma", "cursor": "Cursor",
    "vscode": "VS Code", "visual studio code": "VS Code",
    "chatgpt": "ChatGPT", "claude": "Claude",
    "browser": "Browser", "chrome": "Browser", "google chrome": "Browser",
    "safari": "Browser", "finder": "Finder",
}
# Patterns containing fewer than three distinct contributing people never
# appear, including in counters or suppressed-pattern previews.
MIN_CONTRIBUTORS = 3
MAX_SEQUENCE = 12


def _app(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _APP_NAMES.get(raw, "Other app")


def _day(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return ""
        return parsed.astimezone(timezone.utc).date().isoformat()
    except (ValueError, TypeError):
        return ""


def manager_insights(
    db: Any, *,
    organization_id: str,
    event_limit: int = 25000,
    min_contributors: int = MIN_CONTRIBUTORS,
) -> dict[str, Any]:
    # The privacy floor is hard: a caller cannot request 1- or 2-person views.
    threshold = max(MIN_CONTRIBUTORS, min(100, int(min_contributors)))
    bounded = max(100, min(25000, int(event_limit)))
    rows = load_gateway_evidence(
        db, organization_id=organization_id, actor_id=None,
        since=None, until=None, limit=bounded,
    )
    # A session cannot accidentally combine different people or devices.
    sessions: dict[tuple[str, str, str, str], list[tuple[str, str, float]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        actor = str(row.get("actor_id") or "").strip()
        device = str(row.get("device_id") or "").strip()
        session = str(row.get("session_id") or "").strip()
        day = _day(row.get("observed_at"))
        if not actor or not device or not session or not day:
            continue
        app = _app(row.get("app"))
        duration = max(0.0, min(float(row.get("duration_seconds") or 0), 3600.0))
        sessions[(actor, device, session, day)].append(
            (str(row.get("observed_at") or ""), app, duration)
        )
    clusters: dict[tuple[str, ...], dict[str, Any]] = {}
    for (actor, _device, _session, _day_value), events in sessions.items():
        sequence: list[str] = []
        total = 0.0
        for _time, app, elapsed in sorted(events):
            if app not in {"Other app", "Finder", "Browser"}:
                if not sequence or sequence[-1] != app:
                    sequence.append(app)
            total += elapsed
        if len(set(sequence)) < 2 or len(sequence) < 3:
            continue
        sequence = sequence[:MAX_SEQUENCE]
        key = tuple(sequence)
        summary = clusters.setdefault(key, {
            "actors": set(), "sessions": 0, "seconds": 0.0, "days": set(),
        })
        summary["actors"].add(actor)
        summary["sessions"] += 1
        summary["seconds"] += total
        summary["days"].add(_day_value)
    results: list[dict[str, Any]] = []
    for apps, value in clusters.items():
        contributors = len(value["actors"])
        if contributors < threshold:
            continue
        observed_runs = int(value["sessions"])
        results.append({
            "apps": list(apps),
            "observed_sessions": observed_runs,
            "contributing_people": contributors,
            "days_observed": len(value["days"]),
            "observed_duration_hours": round(float(value["seconds"]) / 3600, 1),
            "automation_candidate": len(set(apps)) >= 3 and observed_runs >= 3,
            "why_review": "Repeated ordered app transitions across distinct reported actors",
            "interpretation": "navigation candidate, not verified task identity or time savings",
        })
    results.sort(key=lambda item: (
        -int(item["contributing_people"]),
        -int(item["observed_sessions"]),
        -float(item["observed_duration_hours"]),
        tuple(item["apps"]),
    ))
    return {
        "organization_id": organization_id,
        "source": "gateway_synced_privacy_hardened_structural_evidence_only",
        "overview": {
            "visible_patterns": len(results),
            "minimum_contributors": threshold,
            "source_row_limit": bounded,
            "source_limit_may_be_reached": len(rows) >= bounded,
            "no_pattern_does_not_mean_no_repeated_work": True,
        },
        "workflows": results[:20],
        "privacy": {
            "suppresses_groups_below_threshold": True,
            "contributors_are_distinct_reported_actor_ids_not_necessarily_verified_people": True,
            "no_personnel_or_individual_performance_reporting": True,
            "contains_no_titles_or_transcripts": True,
            "time_is_sum_of_observed_durations_may_overlap": True,
            "never_claim_automation_savings": True,
            "local_or_paused_evidence_is_not_available": True,
        },
    }
