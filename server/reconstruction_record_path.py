"""Read-only observed source-record path inside an already linked work item.

This projection never constructs a membership edge. A change is counted only
when two adjacent admitted member observations name different literal source
records in the same exact capture scope. Unknown record evidence and capture
boundaries stay visible, rather than becoming invented case transitions.
Record labels are response-local pseudonyms, never source IDs or user text.
"""
from __future__ import annotations

from typing import Any

_MAX_RUNS = 24


def observed_record_path(
    member_rows: list[dict[str, Any]], *, max_runs: int = _MAX_RUNS,
) -> dict[str, Any]:
    """Project chronological member rows without inferring causality or joins."""
    max_runs = max(1, min(int(max_runs), _MAX_RUNS))
    labels: dict[tuple[str, str, str], str] = {}
    runs: list[dict[str, Any]] = []
    count = changes = unknown = scope_boundaries = 0
    current: dict[str, Any] | None = None
    previous_key: tuple[str, str, str] | None = None
    previous_scope: tuple[str, str, str, str] | None = None

    def emit() -> None:
        nonlocal count
        if current is not None:
            count += 1
            if len(runs) < max_runs:
                runs.append(current)

    for row in member_rows:
        # The production linker has already admitted the row, validated its
        # exact source claim and suppressed contradictory record identities.
        key = ((row["ref"], row["provider"], row["kind"])
               if row["ref"] and not row["reference_tainted"] else None)
        scope = row["scope"]
        if key is None:
            unknown += 1
        elif key not in labels:
            labels[key] = f"record_{len(labels) + 1:02d}"

        same_run = (current is not None and scope is not None
                    and scope == previous_scope and key == previous_key)
        if same_run:
            current["last_event_id"] = row["event_id"]
            current["last_observed_at"] = row["observed_at"]
            current["observed_event_count"] += 1
        else:
            boundary: list[str] = []
            if current is not None:
                if scope is None or previous_scope is None or scope != previous_scope:
                    boundary = ["capture_scope_change_or_unverified"]
                    scope_boundaries += 1
                elif key is None or previous_key is None:
                    boundary = ["unknown_record_boundary"]
                else:
                    boundary = ["observed_record_change"]
                    changes += 1
            emit()
            current = {
                "run_index": count + 1,
                "record_id": labels[key] if key is not None else None,
                "record_known": key is not None,
                "capture_scope_verified": scope is not None,
                "boundary_before": boundary,
                "first_event_id": row["event_id"],
                "last_event_id": row["event_id"],
                "first_observed_at": row["observed_at"],
                "last_observed_at": row["observed_at"],
                "observed_event_count": 1,
            }
        previous_key, previous_scope = key, scope
    emit()

    return {
        "observed_record_run_count": count,
        "observed_record_change_count": changes,
        "observed_unknown_record_observation_count": unknown,
        "observed_record_scope_boundary_count": scope_boundaries,
        "observed_record_runs_truncated": count > len(runs),
        "observed_record_runs": runs,
    }


__all__ = ["observed_record_path"]
