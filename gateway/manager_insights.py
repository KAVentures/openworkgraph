"""Privacy-minimized organizational workflow insights from *synced* evidence only.

No endpoint-local read or unsynced history. No personnel ranking, employee IDs,
window titles, free text, raw metadata, file names, sessions, or event references
are returned. Small groups are suppressed to reduce reidentification risk.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from itertools import product
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



# Only safe, recognized hostnames become named business applications.
# No window/page title, URL path, hostname from an unknown domain or message
# content may be used as an output label.
_TRUSTED_SITES = (
    ("mail.google.com", "Gmail"),
    ("gmail.com", "Gmail"),
    ("outlook.office.com", "Outlook"),
    ("outlook.office365.com", "Outlook"),
    ("outlook.live.com", "Outlook"),
    ("fortnox.se", "Fortnox"),
    ("github.com", "GitHub"),
    ("atlassian.net", "Jira"),
    ("jira.com", "Jira"),
    ("salesforce.com", "Salesforce"),
    ("force.com", "Salesforce"),
    ("docs.google.com", "Docs"),
    ("sheets.google.com", "Sheets"),
    ("drive.google.com", "Drive"),
    ("slack.com", "Slack"),
    ("app.slack.com", "Slack"),
    ("linear.app", "Linear"),
    ("notion.so", "Notion"),
    ("figma.com", "Figma"),
)
_IGNORED_APPS = {"Browser", "Finder", "Other app"}
MAX_MOTIF_MINUTES = 90


def _surface(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    page = metadata.get("page") if isinstance(metadata.get("page"), dict) else {}
    hostname = str(
        row.get("page_host") or row.get("browser_hostname")
        or page.get("hostname") or ""
    ).strip().lower().rstrip(".")
    if re.fullmatch(r"[a-z0-9.-]{1,253}", hostname):
        for domain, name in _TRUSTED_SITES:
            if hostname == domain or hostname.endswith("." + domain):
                return name
    # A generic desktop browser cannot override its own page identity.
    return _app(row.get("app"))


def _timestamp(value: Any) -> float | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo is not None else None
    except (ValueError, OverflowError, TypeError):
        return None


def _motifs(events: list[tuple[float, str, float]]):
    """Bounded ordered windows, allowing one incidental event between steps.

    A recording session is not a task. Compare short stable app sequences
    within it, without guessing task completion from one person's full day.
    """
    # Remove same-app duplicates, which are common when the browser and
    # desktop sensors both observe the same page.
    sequence: list[tuple[float, str, float]] = []
    for event in sorted(events):
        if event[1] in _IGNORED_APPS:
            continue
        if sequence and sequence[-1][1] == event[1]:
            at, name, duration = sequence[-1]
            sequence[-1] = (at, name, min(duration + event[2], 3600.0))
        else:
            sequence.append(event)
    found: dict[tuple[str, ...], float] = {}
    # Favor four-stage workflows; a three-stage workflow is still useful
    # when it is the longest observed span. Different source sessions must
    # never be joined into an invented execution.
    for width in (4, 3):
        for start in range(len(sequence)):
            for gaps in product((1, 2), repeat=width - 1):
                indices = [start]
                for gap in gaps:
                    indices.append(indices[-1] + gap)
                if indices[-1] >= len(sequence):
                    continue
                selected = [sequence[index] for index in indices]
                if selected[-1][0] - selected[0][0] > MAX_MOTIF_MINUTES * 60:
                    continue
                key = tuple(item[1] for item in selected)
                if len(set(key)) < 3:
                    continue
                # A 4-step motif is more informative than its 3-step slices.
                if width == 3 and any(
                    len(other) == 4 and other[:3] == key or
                    len(other) == 4 and other[1:] == key for other in found
                ):
                    continue
                duration = sum(item[2] for item in selected)
                found[key] = max(found.get(key, 0), duration)
    # Compact existing full four-stage patterns to avoid duplicate cards.
    return {key: value for key, value in found.items() if
            len(key) == 4 or not any(
                len(other) == 4 and (other[:3] == key or other[1:] == key)
                for other in found
            )}


def manager_insights(
    db: Any, *,
    organization_id: str,
    event_limit: int = 25000,
    min_contributors: int = MIN_CONTRIBUTORS,
) -> dict[str, Any]:
    threshold = max(MIN_CONTRIBUTORS, min(100, int(min_contributors)))
    bounded = max(100, min(25000, int(event_limit)))
    rows = load_gateway_evidence(
        db, organization_id=organization_id, actor_id=None,
        since=None, until=None, limit=bounded,
    )
    sessions: dict[
        tuple[str, str, str, str], list[tuple[float, str, float]]
    ] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        actor = str(row.get("actor_id") or "").strip()
        device = str(row.get("device_id") or "").strip()
        session = str(row.get("session_id") or "").strip()
        day = _day(row.get("observed_at"))
        at = _timestamp(row.get("observed_at"))
        if not all((actor, device, session, day)) or at is None:
            continue
        name = _surface(row)
        try:
            duration = max(0.0, min(float(row.get("duration_seconds") or 0), 3600.0))
        except (TypeError, ValueError, OverflowError):
            duration = 0.0
        sessions[(actor, device, session, day)].append((at, name, duration))

    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for (actor, _device, _session, day), events in sessions.items():
        for key, duration in _motifs(events).items():
            summary = groups.setdefault(
                key, {"actors": set(), "sessions": 0, "seconds": 0.0, "days": set()}
            )
            summary["actors"].add(actor)
            summary["sessions"] += 1
            summary["seconds"] += duration
            summary["days"].add(day)

    # Enforce the k>=3 privacy threshold BEFORE sorting, rendering or
    # reporting any count. Never expose raw actor/device/session identifiers.
    results: list[dict[str, Any]] = []
    for apps, value in groups.items():
        contributors = len(value["actors"])
        if contributors < threshold:
            continue
        observed_runs = int(value["sessions"])
        results.append({
            "apps": list(apps),
            "observed_sessions": observed_runs,
            "contributing_people": contributors,
            "days_observed": len(value["days"]),
            "observed_duration_hours": round(value["seconds"] / 3600, 1),
            "automation_candidate": len(set(apps)) >= 3 and observed_runs >= 3,
            "why_review": "Repeated ordered app transitions across distinct reported actors",
            "interpretation": "navigation candidate, not verified task identity or time savings",
        })
    results.sort(key=lambda item: (
        -len(set(item["apps"])),
        -len(item["apps"]),
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
