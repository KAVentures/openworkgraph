"""Object-centric same-pair event binding continuity over caller-supplied OCEL O2O links.

A directed object pair only has evidence for a selected event when that *same*
event is associated with BOTH of its object IDs. Co-attendance is not inferred
from object-type frequency or from different events in a flattened case.
This is a descriptive recorded-event check, not a full OCPN binding replay.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator
from shared.event_log_formats import EventLogFormatError, _exact_key
from . import event_log_workspace as workspace
from . import event_log_object_relationships as relationships

FORMAT = "openworkgraph.ocel-object-binding-continuity.v1"
MAX_PAIRS = 20_000
MAX_SELECTED_LINKS = 1_000_000
MAX_MATCHED_PAIR_EVENTS = 1_000_000
MAX_SAMPLES = 50


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_type: str = Field(min_length=1, max_length=200)
    target_type: str = Field(min_length=1, max_length=200)
    first_activity: str = Field(min_length=1, max_length=200)
    then_activity: str = Field(min_length=1, max_length=200)
    qualifier: str | None = Field(default=None, max_length=80)
    sample_limit: StrictInt = Field(default=20, ge=0, le=MAX_SAMPLES)

    @field_validator("source_type", "target_type", "first_activity", "then_activity", "qualifier")
    @classmethod
    def literal(cls, value: str | None, info):
        if value is None:
            return None
        try:
            _exact_key(value, 80 if info.field_name == "qualifier" else 200)
        except EventLogFormatError:
            raise ValueError("Use an exact literal imported object type, activity or qualifier") from None
        if not value.strip() and info.field_name != "qualifier":
            raise ValueError("Activity and object-type labels cannot be blank")
        return value


def parse(spec: Any) -> Spec:
    try:
        validated = Spec.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError(
            "Joint event bindings need exact source/target types, two different activity labels, optional qualifier and 0–50 samples"
        ) from None
    if validated.first_activity == validated.then_activity:
        raise workspace.EventLogWorkspaceError(
            "Joint event bindings require two different activities to establish observed ordering")
    return validated


def _stamp(stamp) -> str | None:
    return stamp.isoformat() + "Z" if stamp is not None else None


def analyze(log_id: str, spec: Any) -> dict[str, Any]:
    selected = parse(spec)
    with relationships._read_log(log_id) as (conn, info):
        where = "r.log_id=? AND s.object_type=? AND t.object_type=?"
        args: list[Any] = [log_id, selected.source_type, selected.target_type]
        if selected.qualifier is not None:
            where += " AND r.qualifier=?"
            args.append(selected.qualifier)
        conn.execute("""CREATE OR REPLACE TEMP TABLE jbc_pairs AS
            SELECT DISTINCT r.source_object_id AS source_id,r.target_object_id AS target_id
            FROM object_relationships r
            JOIN objects s ON s.log_id=r.log_id AND s.object_id=r.source_object_id
            JOIN objects t ON t.log_id=r.log_id AND t.object_id=r.target_object_id
            WHERE """ + where, args)
        n_pairs = int(conn.execute("SELECT count(*) FROM jbc_pairs").fetchone()[0])
        if conn.execute("SELECT 1 FROM jbc_pairs WHERE source_id=target_id LIMIT 1").fetchone():
            raise workspace.EventLogWorkspaceError(
                "Self-referential OCEL assertions cannot prove a binding between two distinct objects; narrow the selected cohort")
        if n_pairs > MAX_PAIRS:
            raise workspace.EventLogWorkspaceError(
                f"Binding continuity exceeds {MAX_PAIRS} distinct directed pairs; narrow the selected types or qualifier")

        # Restrict work to the exact pair endpoints and two selected activities.
        # Qualifier duplicates are DISTINCT'ed before any pairing, so one event
        # cannot multiply artificial observations through event-object roles.
        conn.execute("""CREATE OR REPLACE TEMP TABLE jbc_links AS
            SELECT DISTINCT eo.event_id,eo.object_id,e.activity,e.ts
            FROM event_objects eo JOIN events e ON e.log_id=eo.log_id AND e.event_id=eo.event_id
            WHERE eo.log_id=? AND e.activity IN (?,?)
              AND (EXISTS(SELECT 1 FROM jbc_pairs p WHERE p.source_id=eo.object_id)
                OR EXISTS(SELECT 1 FROM jbc_pairs p WHERE p.target_id=eo.object_id))""",
            [log_id, selected.first_activity, selected.then_activity])
        links = int(conn.execute("SELECT count(*) FROM jbc_links").fetchone()[0])
        if links > MAX_SELECTED_LINKS:
            raise workspace.EventLogWorkspaceError(
                f"Binding continuity exceeds {MAX_SELECTED_LINKS} selected event/object links")

        # The shared event ID is the binding witness. Both object IDs must
        # attend exactly that same original event, not merely events at the
        # same timestamp or in the same activity.
        conn.execute("""CREATE OR REPLACE TEMP TABLE jbc_pair_events AS
            SELECT p.source_id,p.target_id,s.event_id,s.activity,s.ts
            FROM jbc_pairs p
            JOIN jbc_links s ON s.object_id=p.source_id
            JOIN jbc_links t ON t.object_id=p.target_id AND t.event_id=s.event_id""")
        pair_events = int(conn.execute("SELECT count(*) FROM jbc_pair_events").fetchone()[0])
        if pair_events > MAX_MATCHED_PAIR_EVENTS:
            raise workspace.EventLogWorkspaceError(
                f"Binding continuity exceeds {MAX_MATCHED_PAIR_EVENTS} jointly attached pair-event witnesses")

        conn.execute("""CREATE OR REPLACE TEMP TABLE jbc_first AS
            SELECT source_id,target_id,
                   min(ts) FILTER (WHERE activity=?) AS first_at,
                   min(ts) FILTER (WHERE activity=?) AS then_at,
                   count(*) FILTER (WHERE activity=?) AS first_events,
                   count(*) FILTER (WHERE activity=?) AS then_events
            FROM jbc_pair_events GROUP BY 1,2""",[
            selected.first_activity,selected.then_activity,
            selected.first_activity,selected.then_activity])
        conn.execute("""CREATE OR REPLACE TEMP TABLE jbc_scored AS
            SELECT p.source_id,p.target_id,f.first_at,f.then_at,
                   coalesce(f.first_events,0) AS first_events,
                   coalesce(f.then_events,0) AS then_events,
                   CASE WHEN f.first_at IS NULL AND f.then_at IS NULL THEN 'neither_joint_event'
                        WHEN f.first_at IS NULL THEN 'then_only'
                        WHEN f.then_at IS NULL THEN 'first_only'
                        WHEN f.then_at<f.first_at THEN 'reversed'
                        WHEN f.then_at=f.first_at THEN 'same_timestamp_order_unknown'
                        ELSE 'forward' END AS status,
                   CASE WHEN f.first_at IS NOT NULL AND f.then_at>f.first_at
                        THEN epoch(f.then_at-f.first_at) ELSE NULL END AS forward_seconds
            FROM jbc_pairs p LEFT JOIN jbc_first f
              ON f.source_id=p.source_id AND f.target_id=p.target_id""")

        count_row=conn.execute("""SELECT count(*),
            count(*) FILTER (WHERE status='forward'),
            count(*) FILTER (WHERE status='reversed'),
            count(*) FILTER (WHERE status='same_timestamp_order_unknown'),
            count(*) FILTER (WHERE status='first_only'),
            count(*) FILTER (WHERE status='then_only'),
            count(*) FILTER (WHERE status='neither_joint_event'),
            count(*) FILTER (WHERE first_events>1 OR then_events>1),
            count(DISTINCT source_id),count(DISTINCT target_id),
            min(forward_seconds),quantile_cont(forward_seconds,0.5),
            quantile_cont(forward_seconds,0.9),max(forward_seconds),
            avg(forward_seconds) FROM jbc_scored""").fetchone()
        fields=["directed_pairs","forward_pairs","reversed_pairs","simultaneous_timestamp_pairs",
                "first_only_pairs","then_only_pairs","neither_joint_event_pairs",
                "pairs_with_repeated_joint_activity","distinct_source_objects","distinct_target_objects"]
        counted={name:int(value) for name,value in zip(fields,count_row[:10])}
        first_min,median,p90,maximum,mean=count_row[10:]
        samples=[]
        if selected.sample_limit:
            rows=conn.execute("""SELECT source_id,target_id,status,first_at,then_at,
                   first_events,then_events,forward_seconds
                FROM jbc_scored
                ORDER BY CASE status WHEN 'reversed' THEN 0
                    WHEN 'same_timestamp_order_unknown' THEN 1
                    WHEN 'first_only' THEN 2 WHEN 'then_only' THEN 3
                    WHEN 'neither_joint_event' THEN 4 ELSE 5 END,
                    source_id,target_id LIMIT ?""",[selected.sample_limit]).fetchall()
            samples=[{"source_object_id":source,"target_object_id":target,"status":status,
                      "first_joint_at":_stamp(a),"then_joint_at":_stamp(b),
                      "first_joint_events":int(na),"then_joint_events":int(nb),
                      "forward_seconds":duration}
                      for source,target,status,a,b,na,nb,duration in rows]
        snap=relationships.snapshot(conn,log_id)
        return {
            "format":FORMAT,"log":info,"settings":selected.model_dump(),
            "relationship_revision":snap["revision"],
            "relationship_digest":snap["relationships_sha256"],
            "object_index_digest":snap["object_index_sha256"],
            **counted,
            "joint_pair_event_witnesses":pair_events,
            "distinct_original_joint_events":int(conn.execute(
                "SELECT count(DISTINCT event_id) FROM jbc_pair_events").fetchone()[0]),
            "selected_distinct_event_object_links":links,
            "forward_elapsed_seconds":{
                "minimum":first_min,"p50":median,"p90":p90,"maximum":maximum,"mean":mean,
            },
            "samples":samples,"samples_truncated":n_pairs>len(samples),
            "co_attendance_requires_same_event_id":True,
            "qualifier_duplicates_count_once_per_pair_event":True,
            "same_timestamp_not_treated_as_proven_order":True,
            "source_data_completeness_verified":False,
            "supplied_relationship_valid_at_event_time_verified":False,
            "case_membership_inferred":False,
            "full_object_centric_petri_net_binding_replay":False,
            "business_conformance_or_policy_verified":False,
            "causality_or_outcome_verified":False,
            "execution_authorized":False,
            "limits":{"max_distinct_pairs":MAX_PAIRS,
                      "max_event_object_links":MAX_SELECTED_LINKS,
                      "max_joined_pair_event_witnesses":MAX_MATCHED_PAIR_EVENTS,
                      "max_samples":MAX_SAMPLES},
            "interpretation":(
                "Each directed pair comes from a caller-supplied OCEL object relationship. "
                "The two selected activities must each have an event actually linked to both "
                "object IDs; two separate events or shared timestamps are not proof of the same "
                "binding. Order and missing witnesses are descriptive. This is NOT full OCPN "
                "binding replay, independently verified policy compliance, a causal handoff, "
                "or proof that the relationship was valid at the event's time."),
        }


__all__=["FORMAT","Spec","MAX_PAIRS","MAX_SELECTED_LINKS","MAX_MATCHED_PAIR_EVENTS","analyze","parse"]
