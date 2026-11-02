"""Read-only, per-record participant x surface evidence for linked work items.

The projection describes *presence*, not work performed, ownership, productivity,
handoff or a verified business case. It never adds membership or joins events.
Only already-admitted exact-record observations with complete capture scope and
a known app surface contribute. Tenant conflicts suppress the whole record.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

_MAX_RECORDS = 12
_MAX_PAIRS_PER_RECORD = 24
_UNKNOWN_SURFACES = {"", "unknown", "other", "other app"}


def _complete_scope(value: Any) -> bool:
    return (isinstance(value, tuple) and len(value) == 4
            and all(isinstance(part, str) and bool(part) for part in value))


def observed_record_participant_surfaces(
    member_rows: list[dict[str, Any]],
    *,
    max_records: int = _MAX_RECORDS,
    max_pairs_per_record: int = _MAX_PAIRS_PER_RECORD,
) -> dict[str, Any]:
    """Return bounded evidence of who was observed on which app per exact ref.

    Input rows are already validated, admitted, UTC-ordered members of ONE
    linked item. Repeated observations do not become a duration or workload
    measure. Response-local pseudonyms never expose raw record, actor, tenant,
    device, session or app values. Unreferenced and tainted observations are
    not record-presence evidence. Time alone supplies no relationship.
    """
    max_records = max(1, min(int(max_records), _MAX_RECORDS))
    max_pairs_per_record = max(1, min(int(max_pairs_per_record), _MAX_PAIRS_PER_RECORD))
    by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in member_rows:
        if row["ref"] and not row["reference_tainted"]:
            by_record[row["ref"]].append(row)

    participant_labels: dict[tuple[str, str], str] = {}
    surface_labels: dict[str, str] = {}
    displayed: list[dict[str, Any]] = []
    record_count = multi_participant_count = 0
    incomplete = unknown_surface = conflicting_tenants = 0
    for record_rows in by_record.values():
        tenants = {row["scope"][0] for row in record_rows
                   if _complete_scope(row["scope"])}
        if len(tenants) > 1:
            # A normalized opaque ref cannot authorize cross-tenant presence.
            conflicting_tenants += 1
            continue

        pairs: dict[tuple[tuple[str, str], str], list[dict[str, Any]]] = {}
        for row in record_rows:
            scope = row["scope"]
            if not _complete_scope(scope):
                incomplete += 1
                continue
            app = row["app"]
            if not isinstance(app, str) or app in _UNKNOWN_SURFACES:
                unknown_surface += 1
                continue
            actor = (scope[0], scope[1])
            key = (actor, app)
            if key not in pairs:
                pairs[key] = [row, row]
            else:
                pairs[key][1] = row

        if not pairs:
            continue
        record_count += 1
        participants = {actor for actor, _app in pairs}
        multi_participant_count += int(len(participants) > 1)
        witnesses: list[dict[str, Any]] = []
        for (actor, app), (first, last) in pairs.items():
            if actor not in participant_labels:
                participant_labels[actor] = f"coverage_participant_{len(participant_labels) + 1:02d}"
            if app not in surface_labels:
                surface_labels[app] = f"coverage_surface_{len(surface_labels) + 1:02d}"
            if len(witnesses) < max_pairs_per_record:
                witnesses.append({
                    "participant_id": participant_labels[actor],
                    "surface_id": surface_labels[app],
                    "first_event_id": first["event_id"],
                    "last_event_id": last["event_id"],
                    "first_observed_at": first["observed_at"],
                    "last_observed_at": last["observed_at"],
                    "continuous_activity_inferred": False,
                })
        if len(displayed) < max_records:
            displayed.append({
                "record_id": f"coverage_record_{record_count:02d}",
                "distinct_observed_participants": len(participants),
                "distinct_observed_surfaces": len({app for _actor, app in pairs}),
                "participant_surface_pair_count": len(pairs),
                "participant_surface_witnesses": witnesses,
                "participant_surface_witnesses_truncated": len(pairs) > len(witnesses),
                "cross_actor_presence_observed": len(participants) > 1,
                "handoff_verified": False,
                "business_outcome_verified": False,
            })

    return {
        "observed_record_coverage_count": record_count,
        "observed_record_coverage": displayed,
        "observed_record_coverage_truncated": record_count > len(displayed),
        "observed_multi_participant_record_count": multi_participant_count,
        "incomplete_scope_record_observations_excluded": incomplete,
        "unknown_surface_record_observations_excluded": unknown_surface,
        "cross_tenant_record_coverage_suppressed": conflicting_tenants,
        "record_coverage_is_observed_presence_not_workload": True,
    }


__all__ = ["observed_record_participant_surfaces"]
