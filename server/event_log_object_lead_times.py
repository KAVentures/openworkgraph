"""Observed cross-object lead times over explicitly supplied OCEL O2O relationships.

Unlike a flattened case-log timing comparison, each unit is one DISTINCT
caller-supplied source/target object pair. No relationship is inferred from
co-occurring events, app visits, timestamps or activity-name similarity.

This is a descriptive read of imported history, not evidence that a supplied
relationship was valid at the time, causal, complete, or approved for action.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

from shared.event_log_formats import EventLogFormatError, _exact_key
from . import event_log_workspace as workspace
from . import event_log_object_relationships as relationships

FORMAT = "openworkgraph.object-relationship-lead-times.v1"
MAX_PAIRS = 20_000
MAX_EVENT_LINKS = 1_000_000
MAX_EXAMPLES = 50


class LeadTimeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_type: str = Field(min_length=1, max_length=200)
    target_type: str = Field(min_length=1, max_length=200)
    source_activity: str = Field(min_length=1, max_length=200)
    target_activity: str = Field(min_length=1, max_length=200)
    qualifier: str | None = Field(default=None, max_length=80)
    sample_limit: StrictInt = Field(default=20, ge=0, le=MAX_EXAMPLES)

    @field_validator("source_type", "target_type", "source_activity", "target_activity", "qualifier")
    @classmethod
    def literal_key(cls, value: str | None, info):
        if value is None:
            return None
        try:
            exact = _exact_key(value, 80 if info.field_name == "qualifier" else 200)
        except EventLogFormatError as exc:
            raise ValueError("unsupported literal identifier or activity") from exc
        if not exact and info.field_name != "qualifier":
            raise ValueError("a nonblank literal label is required")
        return exact


def parse(spec: Any) -> LeadTimeSpec:
    try:
        return LeadTimeSpec.model_validate(spec)
    except ValidationError as exc:
        problem = exc.errors()[0]
        field = ".".join(str(v) for v in problem["loc"])
        raise workspace.EventLogWorkspaceError(
            "object lead times: invalid " + (field or "request") +
            " (literal source/target types and activities, optional qualifier, sample_limit 0–50)"
        ) from None


def _timestamp(value) -> str | None:
    return value.isoformat() + "Z" if value is not None else None


def analyze(log_id: str, spec: Any) -> dict[str, Any]:
    requested = parse(spec)
    params = requested.model_dump()
    with relationships._read_log(log_id) as (conn, info):
        # Preserve exact supplied O2O direction and selected qualifier. A pair
        # with multiple qualifiers is still one pair, not several observations.
        where = """r.log_id = ? AND s.object_type = ? AND t.object_type = ?"""
        args: list[Any] = [log_id, requested.source_type, requested.target_type]
        if requested.qualifier is not None:
            where += " AND r.qualifier = ?"
            args.append(requested.qualifier)
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE owg_lt_pairs AS
            SELECT DISTINCT r.source_object_id AS source_id, r.target_object_id AS target_id
            FROM object_relationships r
            JOIN objects s ON s.log_id = r.log_id AND s.object_id = r.source_object_id
            JOIN objects t ON t.log_id = r.log_id AND t.object_id = r.target_object_id
            WHERE {where}""", args)
        pair_count = int(conn.execute("SELECT count(*) FROM owg_lt_pairs").fetchone()[0])
        if pair_count > MAX_PAIRS:
            raise workspace.EventLogWorkspaceError(
                f"object lead times need at most {MAX_PAIRS} distinct relationship pairs; "
                "filter by types/qualifier or use a smaller declared source")

        # Scope each aggregate to the selected set of objects. COUNT DISTINCT
        # prevents OCEL event-object qualifiers from duplicating an occurrence.
        for side, activity in (("source", requested.source_activity),
                               ("target", requested.target_activity)):
            object_column = "source_id" if side == "source" else "target_id"
            conn.execute(f"""CREATE OR REPLACE TEMP TABLE owg_lt_{side} AS
                SELECT eo.object_id, min(e.ts) AS first_at, count(DISTINCT e.event_id) AS occurrences
                FROM event_objects eo
                JOIN events e ON e.log_id = eo.log_id AND e.event_id = eo.event_id
                WHERE eo.log_id = ? AND e.activity = ?
                  AND EXISTS (SELECT 1 FROM owg_lt_pairs p WHERE p.{object_column} = eo.object_id)
                GROUP BY eo.object_id""", [log_id, activity])

        # Refuse an oversized computation rather than presenting an apparently
        # complete subset. The pair cap applies separately from event links.
        links = sum(int(conn.execute(
            f"SELECT coalesce(sum(occurrences), 0) FROM owg_lt_{side}").fetchone()[0])
                    for side in ("source", "target"))
        if links > MAX_EVENT_LINKS:
            raise workspace.EventLogWorkspaceError(
                f"object lead times exceed the {MAX_EVENT_LINKS} selected event-link budget; "
                "choose a narrower source/target relationship cohort")

        conn.execute("""CREATE OR REPLACE TEMP TABLE owg_lt_scored AS
            SELECT p.source_id, p.target_id, s.first_at AS source_at, t.first_at AS target_at,
                coalesce(s.occurrences, 0)::BIGINT AS source_occurrences,
                coalesce(t.occurrences, 0)::BIGINT AS target_occurrences,
                CASE WHEN s.first_at IS NULL AND t.first_at IS NULL THEN 'both_missing'
                     WHEN s.first_at IS NULL THEN 'source_missing'
                     WHEN t.first_at IS NULL THEN 'target_missing'
                     WHEN t.first_at < s.first_at THEN 'reversed'
                     ELSE 'forward' END AS status,
                CASE WHEN s.first_at IS NOT NULL AND t.first_at >= s.first_at
                     THEN epoch(t.first_at - s.first_at) ELSE NULL END AS lead_seconds
            FROM owg_lt_pairs p
            LEFT JOIN owg_lt_source s ON s.object_id = p.source_id
            LEFT JOIN owg_lt_target t ON t.object_id = p.target_id""")

        row = conn.execute("""SELECT count(*) AS total,
                count(*) FILTER (WHERE status = 'forward') AS forward,
                count(*) FILTER (WHERE status = 'reversed') AS reversed,
                count(*) FILTER (WHERE status = 'source_missing') AS source_missing,
                count(*) FILTER (WHERE status = 'target_missing') AS target_missing,
                count(*) FILTER (WHERE status = 'both_missing') AS both_missing,
                count(*) FILTER (WHERE source_occurrences > 1 OR target_occurrences > 1) AS repeated,
                count(*) FILTER (WHERE status = 'forward' AND lead_seconds = 0) AS simultaneous,
                min(lead_seconds) AS minimum, quantile_cont(lead_seconds, 0.5) AS median,
                quantile_cont(lead_seconds, 0.9) AS p90, max(lead_seconds) AS maximum,
                avg(lead_seconds) AS mean
            FROM owg_lt_scored""").fetchone()
        total, forward, reversed_count, no_src, no_target, neither, repeated, simultaneous, minimum, median, p90, maximum, mean = row

        examples: list[dict[str, Any]] = []
        if requested.sample_limit:
            rows = conn.execute("""SELECT source_id, target_id, source_at, target_at,
                       status, source_occurrences, target_occurrences, lead_seconds
                FROM owg_lt_scored
                ORDER BY CASE status WHEN 'reversed' THEN 0
                                     WHEN 'target_missing' THEN 1
                                     WHEN 'source_missing' THEN 2
                                     WHEN 'both_missing' THEN 3
                                     ELSE 4 END,
                         lead_seconds DESC NULLS LAST, source_id, target_id
                LIMIT ?""", [requested.sample_limit]).fetchall()
            examples = [{"source_object_id": source, "target_object_id": target,
                         "source_first_at": _timestamp(src_at), "target_first_at": _timestamp(dst_at),
                         "status": status, "source_event_occurrences": src_count,
                         "target_event_occurrences": dst_count, "lead_seconds": lag}
                        for source, target, src_at, dst_at, status, src_count, dst_count, lag in rows]
        snap = relationships.snapshot(conn, log_id)
        return {
            "format": FORMAT, "log": info, "settings": params,
            "relationship_revision": snap["revision"],
            "relationship_digest": snap["relationships_sha256"],
            "object_index_digest": snap["object_index_sha256"],
            "pair_basis": "unique_directed_caller_supplied_object_relationship_pairs",
            "pairs": int(total),
            "forward_pairs": int(forward), "reversed_pairs": int(reversed_count),
            "source_activity_missing_pairs": int(no_src),
            "target_activity_missing_pairs": int(no_target),
            "both_activities_missing_pairs": int(neither),
            "pairs_with_repeated_selected_activity": int(repeated),
            "same_timestamp_pairs": int(simultaneous),
            "forward_lead_time_seconds": {
                "minimum": minimum, "p50": median, "p90": p90, "maximum": maximum,
                "mean": mean,
            },
            "samples": examples,
            "sample_truncated": int(total) > len(examples),
            "limits": {"max_distinct_pairs": MAX_PAIRS, "max_selected_event_links": MAX_EVENT_LINKS,
                       "max_samples": MAX_EXAMPLES},
            "computation_complete_for_selected_pairs": True,
            "first_occurrence_only": True,
            "negative_ordering_excluded_from_forward_percentiles": True,
            "zero_lag_is_not_proof_of_handoff": True,
            "supplied_relationship_validity_period_known": False,
            "source_data_completeness_verified": False,
            "causal_claims": False, "employee_performance_measure": False,
            "execution_authorized": False,
            "interpretation": (
                "Elapsed time between the first selected source and target activities for each distinct "
                "caller-supplied related object pair. Reversed and missing activity evidence is excluded "
                "from forward time percentiles. These imported timestamps and declared links do not "
                "establish a causal handoff, complete history or verified business relationship."
            ),
        }


__all__ = ["FORMAT", "MAX_PAIRS", "MAX_EVENT_LINKS", "LeadTimeSpec", "parse", "analyze"]
