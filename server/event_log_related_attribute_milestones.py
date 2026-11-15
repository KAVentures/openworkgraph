"""OCEL: target-object attribute state at source/target milestones for supplied O2O pairs.

Each DISTINCT directed, caller-supplied source/target object pair is one unit,
irrespective of repeated event-object qualifiers or duplicate relationship
qualifiers. Static OCEL O2O relationships have no observed validity interval:
this is a retrospective comparison, NOT an as-of relationship-membership check.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator
from shared.event_log_formats import EventLogFormatError, _exact_key
from . import event_log_workspace as workspace
from . import event_log_object_relationships as relationships

FORMAT = "openworkgraph.related-object-attribute-milestones.v1"
MAX_PAIRS = 20_000
MAX_LINKS = 1_000_000
MAX_ATTRIBUTE_VALUES = 250_000
MAX_GROUPS = 20
MAX_EXAMPLES = 50


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_type: str = Field(min_length=1, max_length=200)
    target_type: str = Field(min_length=1, max_length=200)
    source_activity: str = Field(min_length=1, max_length=200)
    target_activity: str = Field(min_length=1, max_length=200)
    target_attribute: str = Field(min_length=1, max_length=200)
    qualifier: str | None = Field(default=None, max_length=80)
    sample_limit: StrictInt = Field(default=20, ge=0, le=MAX_EXAMPLES)

    @field_validator("source_type", "target_type", "source_activity",
                     "target_activity", "target_attribute", "qualifier")
    @classmethod
    def literal_key(cls, value: str | None, info):
        if value is None:
            return None
        try:
            _exact_key(value, 80 if info.field_name == "qualifier" else 200)
        except EventLogFormatError:
            raise ValueError("A literal imported label is required") from None
        if not value.strip() and info.field_name != "qualifier":
            raise ValueError("A nonblank literal imported label is required")
        return value


def _parse(spec: Any) -> Spec:
    try:
        return Spec.model_validate(spec)
    except ValidationError as exc:
        where = ".".join(str(part) for part in exc.errors()[0]["loc"])
        raise workspace.EventLogWorkspaceError(
            "Related object as-of settings are invalid: " + where +
            " (choose literal object types, activities, target attribute, qualifier and 0–50 examples)"
        ) from None


def _iso(stamp):
    return stamp.isoformat() + "Z" if stamp is not None else None


def analyze(log_id: str, spec: Any) -> dict[str, Any]:
    chosen = _parse(spec)
    with relationships._read_log(log_id) as (conn, info):
        where = """r.log_id=? AND src.object_type=? AND tgt.object_type=?"""
        args = [log_id, chosen.source_type, chosen.target_type]
        if chosen.qualifier is not None:
            where += " AND r.qualifier=?"
            args.append(chosen.qualifier)
        conn.execute("""CREATE OR REPLACE TEMP TABLE ram_pairs AS
            SELECT DISTINCT r.source_object_id AS source_id,r.target_object_id AS target_id
            FROM object_relationships r
            JOIN objects src ON src.log_id=r.log_id AND src.object_id=r.source_object_id
            JOIN objects tgt ON tgt.log_id=r.log_id AND tgt.object_id=r.target_object_id
            WHERE """ + where, args)
        pairs = int(conn.execute("SELECT count(*) FROM ram_pairs").fetchone()[0])
        if pairs > MAX_PAIRS:
            raise workspace.EventLogWorkspaceError(
                f"Attribute milestones need at most {MAX_PAIRS} distinct directed pairs; select a narrower qualifier/type cohort")

        for side, activity in (("source", chosen.source_activity), ("target", chosen.target_activity)):
            col = "source_id" if side == "source" else "target_id"
            conn.execute(f"""CREATE OR REPLACE TEMP TABLE ram_{side} AS
                SELECT eo.object_id, min(e.ts) AS first_at,
                       count(DISTINCT e.event_id) AS occurrences
                FROM event_objects eo JOIN events e
                  ON e.log_id=eo.log_id AND e.event_id=eo.event_id
                WHERE eo.log_id=? AND e.activity=? AND
                    EXISTS (SELECT 1 FROM ram_pairs p WHERE p.{col}=eo.object_id)
                GROUP BY 1""", [log_id, activity])
        links = sum(int(conn.execute(
            f"SELECT coalesce(sum(occurrences),0) FROM ram_{side}").fetchone()[0])
            for side in ("source", "target"))
        if links > MAX_LINKS:
            raise workspace.EventLogWorkspaceError(
                f"Related attribute milestones exceed the {MAX_LINKS} selected event-link work budget")

        value_count = int(conn.execute("""SELECT count(*) FROM object_attribute_values v
            WHERE v.log_id=? AND v.name=? AND EXISTS
              (SELECT 1 FROM ram_pairs p WHERE p.target_id=v.object_id)""",
            [log_id, chosen.target_attribute]).fetchone()[0])
        if value_count > MAX_ATTRIBUTE_VALUES:
            raise workspace.EventLogWorkspaceError(
                f"Related attribute milestones exceed the {MAX_ATTRIBUTE_VALUES} selected timed-value work budget")

        # Multiple literal values at the same effective time cannot be ordered
        # reliably. They remain ambiguous rather than choosing an arbitrary one.
        conn.execute("""CREATE OR REPLACE TEMP TABLE ram_history AS
            SELECT v.object_id, v.valid_from,
                   CASE WHEN count(DISTINCT v.value)=1 THEN min(v.value) END AS value,
                   count(DISTINCT v.value)>1 AS ambiguous
            FROM object_attribute_values v
            WHERE v.log_id=? AND v.name=?
              AND EXISTS (SELECT 1 FROM ram_pairs p WHERE p.target_id=v.object_id)
            GROUP BY 1,2""", [log_id, chosen.target_attribute])
        conn.execute("""CREATE OR REPLACE TEMP TABLE ram_milestones AS
            SELECT p.source_id,p.target_id,s.first_at AS source_at,t.first_at AS target_at,
                   coalesce(s.occurrences,0) AS source_occurrences,
                   coalesce(t.occurrences,0) AS target_occurrences
            FROM ram_pairs p LEFT JOIN ram_source s ON s.object_id=p.source_id
              LEFT JOIN ram_target t ON t.object_id=p.target_id""")

        # ASOF joins look up the latest recorded value no later than each
        # selected event time, irrespective of the order of OCEL input rows.
        # They do NOT imply that the O2O relationship existed at that time.
        conn.execute("""CREATE OR REPLACE TEMP TABLE ram_asof_source AS
            SELECT p.*, h.valid_from AS source_value_from,
                   h.value AS source_value,coalesce(h.ambiguous,false) AS source_ambiguous
            FROM ram_milestones p
            ASOF LEFT JOIN ram_history h
              ON p.target_id=h.object_id AND p.source_at>=h.valid_from""")
        conn.execute("""CREATE OR REPLACE TEMP TABLE ram_asof_both AS
            SELECT p.*,h.valid_from AS target_value_from,h.value AS target_value,
                   coalesce(h.ambiguous,false) AS target_ambiguous
            FROM ram_asof_source p
            ASOF LEFT JOIN ram_history h
              ON p.target_id=h.object_id AND p.target_at>=h.valid_from""")
        conn.execute("""CREATE OR REPLACE TEMP TABLE ram_state AS
            SELECT p.*, CASE
                  WHEN source_at IS NULL THEN 'source_activity_missing'
                  WHEN source_value_from IS NULL AND has_history.object_id IS NULL THEN 'attribute_never_recorded'
                  WHEN source_value_from IS NULL THEN 'attribute_recorded_only_later'
                  WHEN source_ambiguous THEN 'ambiguous_same_effective_time'
                  ELSE 'known'
                END AS at_source_status,
                CASE
                  WHEN target_at IS NULL THEN 'target_activity_missing'
                  WHEN target_value_from IS NULL AND has_history.object_id IS NULL THEN 'attribute_never_recorded'
                  WHEN target_value_from IS NULL THEN 'attribute_recorded_only_later'
                  WHEN target_ambiguous THEN 'ambiguous_same_effective_time'
                  ELSE 'known'
                END AS at_target_status
            FROM ram_asof_both p LEFT JOIN
              (SELECT DISTINCT object_id FROM ram_history) has_history
              ON has_history.object_id=p.target_id""")

        counts = conn.execute("""SELECT count(*),
              count(*) FILTER (WHERE at_source_status='known'),
              count(*) FILTER (WHERE at_target_status='known'),
              count(*) FILTER (WHERE at_source_status='source_activity_missing'),
              count(*) FILTER (WHERE at_target_status='target_activity_missing'),
              count(*) FILTER (WHERE at_source_status='attribute_never_recorded'),
              count(*) FILTER (WHERE at_source_status='attribute_recorded_only_later'),
              count(*) FILTER (WHERE at_source_status='ambiguous_same_effective_time'),
              count(*) FILTER (WHERE at_source_status='known' AND at_target_status='known'
                                       AND source_value IS DISTINCT FROM target_value),
              count(*) FILTER (WHERE at_source_status='known' AND at_target_status='known'
                                       AND source_value IS NOT DISTINCT FROM target_value),
              count(*) FILTER (WHERE source_at IS NOT NULL AND target_at IS NOT NULL AND target_at<source_at),
              count(DISTINCT source_id),count(DISTINCT target_id),
              count(*) FILTER (WHERE at_source_status='known'
                AND source_value_from=TIMESTAMP '1970-01-01 00:00:00')
            FROM ram_state""").fetchone()
        labels = ["pairs","known_at_source_pairs","known_at_target_pairs",
                  "missing_source_activity_pairs","missing_target_activity_pairs",
                  "target_attribute_never_recorded_pairs","target_attribute_recorded_later_pairs",
                  "ambiguous_same_timestamp_pairs","different_known_values_across_milestones_pairs",
                  "same_known_values_across_milestones_pairs","reversed_activity_order_pairs",
                  "distinct_source_objects","distinct_target_objects",
                  "initial_untimed_value_used_at_source_pairs"]
        summary = {name:int(n) for name,n in zip(labels,counts)}

        groups = conn.execute("""SELECT at_source_status,
                 CASE WHEN at_source_status='known' THEN source_value ELSE NULL END AS value,
                 count(*) AS n
            FROM ram_state GROUP BY 1,2 ORDER BY 3 DESC,1,2 LIMIT ?""",
            [MAX_GROUPS+1]).fetchall()
        listed = [{"status":status,"value":value,"pairs":int(n)}
                  for status,value,n in groups[:MAX_GROUPS]]
        samples=[]
        if chosen.sample_limit:
            rows=conn.execute("""SELECT source_id,target_id,source_at,target_at,
                       at_source_status,source_value,at_target_status,target_value,
                       source_value_from,target_value_from
                FROM ram_state ORDER BY
                  CASE at_source_status WHEN 'ambiguous_same_effective_time' THEN 0
                       WHEN 'attribute_recorded_only_later' THEN 1
                       WHEN 'attribute_never_recorded' THEN 2
                       WHEN 'source_activity_missing' THEN 3 ELSE 4 END,
                  source_id,target_id LIMIT ?""",[chosen.sample_limit]).fetchall()
            for source,target,first,second,status,value,tstatus,tvalue,first_since,second_since in rows:
                samples.append({"source_object_id":source,"target_object_id":target,
                    "source_first_activity_at":_iso(first),"target_first_activity_at":_iso(second),
                    "at_source_status":status,"target_value_at_source_activity":value if status=='known' else None,
                    "at_target_status":tstatus,"target_value_at_target_activity":tvalue if tstatus=='known' else None,
                    "source_value_effective_from":_iso(first_since) if status=='known' else None,
                    "target_value_effective_from":_iso(second_since) if tstatus=='known' else None})
        snap = relationships.snapshot(conn,log_id)
        return {"format":FORMAT,"log":info,"settings":chosen.model_dump(),
            "relationship_revision":snap["revision"],
            "relationship_digest":snap["relationships_sha256"],
            "object_index_digest":snap["object_index_sha256"],
            **summary,"at_source_value_groups":listed,
            "at_source_groups_truncated":len(groups)>MAX_GROUPS,
            "sample_pairs":samples,"samples_truncated":pairs>len(samples),
            "selected_event_link_count":links,"selected_timed_value_count":value_count,
            "unit":"distinct_directed_caller_supplied_object_pair",
            "relationship_qualifiers_do_not_duplicate_pairs":True,
            "target_attribute_read_asof_each_observed_activity":True,
            "same_timestamp_competing_values_are_ambiguous":True,
            "initial_1970_values_have_unknown_business_start_time":True,
            "relationship_validity_at_event_time_verified":False,
            "independent_source_state_verified":False,
            "historical_completeness_verified":False,
            "causal_claims":False,"business_action_authorized":False,
            "limits":{"max_distinct_pairs":MAX_PAIRS,"max_selected_event_links":MAX_LINKS,
                      "max_selected_timed_values":MAX_ATTRIBUTE_VALUES,
                      "max_value_groups_displayed":MAX_GROUPS,"max_examples":MAX_EXAMPLES},
            "interpretation":(
                "The target object's imported timed attribute at the first selected source and target "
                "activities, measured once for each distinct directed supplied pair. Repeated qualifiers "
                "and event-object links do not create extra pairs. Unknown, missing and simultaneous "
                "competing values are not silently inferred. O2O relationships are static imported assertions: "
                "no validity interval, business outcome, causality or contemporaneous relationship is verified."),
        }


__all__ = ["FORMAT","Spec","MAX_PAIRS","MAX_LINKS","MAX_ATTRIBUTE_VALUES","analyze"]
