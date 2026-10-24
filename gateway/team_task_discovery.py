"""Team-level task discovery from synced, k-anonymous aggregates (roadmap #326 A4).

Finds ordered app motifs that several people in a team repeat, and the motifs
that recur independently across teams, from evidence people already chose to
sync. Every reported cell needs at least k distinct contributors inside that
team; smaller teams and smaller cells are suppressed without counts.

Extra limits beyond the organization-level manager insights:
- only complete UTC ISO weeks (default: the last four), so the current partial
  week cannot be differenced against a later read;
- a person's work counts for a team only from the day after their current
  team assignment, so a team cannot be assembled after the fact around one
  person's past work;
- counts and durations are rounded, so subtracting one team from the
  organization total does not reveal a suppressed cell exactly.

Thresholding and rounding reduce disclosure risk; this is not anonymization or
differential privacy, and repeated people-level grouping by an administrator
can still narrow down individuals. Every read is audited.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .human_access import (
    _aggregate_window, _coarse_count, _coarse_duration, _utc_datetime, init_human_access_schema,
)
from .manager_insights import MIN_CONTRIBUTORS, session_motifs
from .workflow_evidence import load_gateway_evidence

DEFAULT_WEEKS = 4
MAX_TEAMS = 200


def team_members(db: Any, organization_id: str, *, assigned_before: str) -> dict[str, dict[str, str]]:
    """Team -> {actor: UTC assignment date} for assignments made before ``assigned_before``."""
    init_human_access_schema(db)
    with db.connect() as conn:
        rows = db._execute(
            conn,
            "SELECT team_id, actor_id, updated_at FROM gateway_actor_teams WHERE organization_id = ?",
            (organization_id,),
        ).fetchall()
    cutoff = _utc_datetime(assigned_before)
    teams: dict[str, dict[str, str]] = defaultdict(dict)
    for team, actor, updated in rows:
        teams[str(team)]  # every known team is listed, even with no eligible member
        try:
            assigned = _utc_datetime(str(updated))
        except (TypeError, ValueError):
            continue
        if assigned <= cutoff and str(actor).strip():
            teams[str(team)][str(actor)] = assigned.date().isoformat()
    return teams


def _window(db: Any, organization_id: str, since: str | None, until: str | None,
            weeks: int) -> tuple[str | None, str | None]:
    if since is None:
        reference = _utc_datetime(until) if until else datetime.now(timezone.utc)
        since = (reference - timedelta(days=7 * weeks)).isoformat()
    return _aggregate_window(db, organization_id, since, until)


def team_task_discovery(
    db: Any, *,
    organization_id: str,
    since: str | None = None,
    until: str | None = None,
    teams: set[str] | None = None,
    min_contributors: int = MIN_CONTRIBUTORS,
    weeks: int = DEFAULT_WEEKS,
    event_limit: int = 25_000,
    limit: int = 10,
) -> dict[str, Any]:
    """Return per-team and cross-team motifs meeting the contributor floor.

    ``teams`` restricts the report to teams the caller may read; ``None`` means
    every team (administrator view).
    """
    k = max(MIN_CONTRIBUTORS, min(100, int(min_contributors)))
    weeks = max(1, min(26, int(weeks)))
    bounded = max(100, min(100_000, int(event_limit)))
    limit = max(1, min(50, int(limit)))
    window_since, window_until = _window(db, organization_id, since, until, weeks)
    report: dict[str, Any] = {
        "organization_id": organization_id,
        "source": "gateway_synced_privacy_hardened_structural_evidence_only",
        "minimum_contributors": k,
        "aggregate_window": "complete_utc_iso_weeks",
        "effective_since": window_since,
        "effective_until": window_until,
        "teams": [],
        "suppressed_teams": [],
        "cross_team_patterns": [],
        "values_rounded": True,
        "rounding": {"counts_to_nearest": 5, "minimum_reported_count": k, "duration_seconds_to_nearest": 900},
        "privacy": {
            "team_cells_below_minimum_are_suppressed_without_counts": True,
            "work_counts_only_after_team_assignment": True,
            "actor_identifiers_returned": False,
            "no_personnel_or_individual_performance_reporting": True,
            "contains_no_titles_or_transcripts": True,
            "not_anonymization_or_differential_privacy": True,
            "local_or_paused_evidence_is_not_available": True,
        },
        "interpretation": "repeated ordered app transitions; navigation candidates, not verified tasks or time savings",
        "derived": True,
        "authoritative": False,
    }
    if not window_since or not window_until or window_since > window_until:
        report["suppression_reason"] = "no_complete_utc_week_in_requested_window"
        return report

    membership = team_members(db, organization_id, assigned_before=window_until)
    if teams is not None:
        membership = {team: actors for team, actors in membership.items() if team in teams}
    eligible = {team: actors for team, actors in sorted(membership.items())[:MAX_TEAMS] if len(actors) >= k}
    report["suppressed_teams"] = sorted(
        {team for team in (teams if teams is not None else membership)} - set(eligible))
    if not eligible:
        report["notice"] = "No permitted team has enough members assigned before the end of the window."
        return report

    team_of: dict[str, dict[str, str]] = defaultdict(dict)
    for team, actors in eligible.items():
        for actor, assigned_day in actors.items():
            team_of[actor][team] = assigned_day
    rows = load_gateway_evidence(
        db, organization_id=organization_id, actor_id=None,
        since=window_since, until=window_until, limit=bounded,
    )
    report["source_limit_may_be_reached"] = len(rows) >= bounded
    cells: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for actor, day, key, duration in session_motifs([row for row in rows if row.get("actor_id") in team_of]):
        for team, assigned_day in team_of[actor].items():
            if day <= assigned_day:
                continue
            cell = cells.setdefault((team, key), {"actors": set(), "sessions": 0, "seconds": 0.0})
            cell["actors"].add(actor)
            cell["sessions"] += 1
            cell["seconds"] += duration

    # Threshold before anything is counted, sorted or reported.
    visible: dict[str, list[dict[str, Any]]] = defaultdict(list)
    teams_per_motif: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for (team, key), cell in cells.items():
        if len(cell["actors"]) < k:
            continue
        teams_per_motif[key].add(team)
        visible[team].append({
            "apps": list(key),
            "contributing_people": _coarse_count(len(cell["actors"]), minimum=k),
            "observed_sessions": _coarse_count(cell["sessions"], minimum=k),
            "observed_duration_seconds": _coarse_duration(cell["seconds"]),
            "automation_candidate": len(set(key)) >= 3 and cell["sessions"] >= 3,
        })
    for team in sorted(eligible):
        patterns = sorted(visible.get(team, []), key=lambda item: (
            -item["contributing_people"], -item["observed_sessions"], -len(set(item["apps"])), item["apps"]))
        for pattern in patterns:
            pattern["also_in_other_teams"] = len(teams_per_motif[tuple(pattern["apps"])]) - 1
        report["teams"].append({
            "team_id": team,
            "members_meet_minimum": True,
            "visible_patterns": len(patterns),
            "patterns": patterns[:limit],
            "no_pattern_does_not_mean_no_repeated_work": True,
        })
    shared = [(key, found) for key, found in teams_per_motif.items() if len(found) >= 2]
    shared.sort(key=lambda item: (-len(item[1]), -len(set(item[0])), item[0]))
    report["cross_team_patterns"] = [
        {"apps": list(key), "teams": sorted(found), "team_count": len(found),
         "each_team_meets_minimum_independently": True}
        for key, found in shared[:limit]
    ]
    return report


__all__ = ["team_members", "team_task_discovery"]
