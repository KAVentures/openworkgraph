"""Observed app/surface runs for already evidence-linked work items.

A surface run is a sequence of validated member observations, not a new
membership edge or a verified task. An app label can be an opaque input claim:
only per-response pseudonyms leave this module, never raw app strings.
Unknown apps and unverified capture scopes cannot establish continuity.
"""
from __future__ import annotations

from typing import Any

_MAX_RUNS = 24
_MAX_EVENT_IDS = 20


def observed_surface_runs(
    member_rows: list[dict[str, Any]],
    *,
    max_runs: int = _MAX_RUNS,
) -> dict[str, Any]:
    """Return bounded, provenance-bearing surface runs for one linked item.

    Member rows are already admitted, UTC-ordered observations from the
    production linker; membership is already decided. Scope equality is meaningful only when all
    four local capture identity components are present. Missing or invalid
    scope never becomes a wildcard. An unknown app never becomes "Other app"
    and cannot prove two consecutive events used the same surface.
    """
    max_runs = max(1, min(int(max_runs), _MAX_RUNS))
    if not member_rows:
        return {
            "observed_surface_run_count": 0,
            "observed_surface_runs": [],
            "observed_surface_runs_truncated": False,
            "observed_app_identity_change_count": 0,
            "observed_unknown_surface_observation_count": 0,
            "observed_surface_scope_boundary_count": 0,
        }

    labels: dict[str, str] = {}
    runs: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    boundary: list[str] = []
    total = app_changes = unknown = scope_boundaries = 0

    def verified_scope(row: dict[str, Any]) -> bool:
        scope = row["scope"]
        return (isinstance(scope, tuple) and len(scope) == 4
                and all(isinstance(value, str) and bool(value) for value in scope))

    def emit() -> None:
        if not current or len(runs) >= max_runs:
            return
        app = current[0]["app"]
        if app and app not in labels:
            labels[app] = f"surface_{len(labels) + 1:02d}"
        runs.append({
            "run_index": len(runs) + 1,
            "boundary_before": list(boundary),
            "surface_id": labels.get(app) if app else None,
            "surface_known": bool(app),
            "capture_scope_verified": verified_scope(current[0]),
            "first_event_id": current[0]["event_id"],
            "last_event_id": current[-1]["event_id"],
            "first_observed_at": current[0]["observed_at"],
            "last_observed_at": current[-1]["observed_at"],
            "observed_event_count": len(current),
            "event_ids": [row["event_id"] for row in current[:_MAX_EVENT_IDS]],
            "event_ids_truncated": len(current) > _MAX_EVENT_IDS,
        })

    for row in member_rows:
        app = row["app"] if isinstance(row.get("app"), str) else ""
        if not app:
            unknown += 1
        reasons: list[str] = []
        if previous is not None:
            # Both endpoints must be independently scoped; unknown scopes
            # cannot claim an observed app transition or continuity.
            if (not verified_scope(previous) or not verified_scope(row)
                    or previous["scope"] != row["scope"]):
                reasons.append("capture_scope_change_or_unverified")
                scope_boundaries += 1
            elif not previous["app"] or not app:
                reasons.append("unknown_surface_boundary")
            elif previous["app"] != app:
                reasons.append("app_identity_change")
                app_changes += 1
        if not current or reasons or not app:
            if current:
                emit()
            total += 1
            current = []
            boundary = reasons or (["unknown_surface"] if not app and previous is None else [])
        current.append(row)
        previous = row
    emit()
    return {
        "observed_surface_run_count": total,
        "observed_surface_runs": runs,
        "observed_surface_runs_truncated": total > len(runs),
        "observed_app_identity_change_count": app_changes,
        "observed_unknown_surface_observation_count": unknown,
        "observed_surface_scope_boundary_count": scope_boundaries,
    }


__all__ = ["observed_surface_runs"]
