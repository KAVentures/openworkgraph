"""Exact cross-log comparison of supplied static O2O assertions.

No inferred identity: comparison requires explicit same-namespace declaration.
A retained assertion absent from a second export is not a verified removal.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from . import event_log_workspace as workspace
from . import event_log_object_relationships as relationships
from shared.event_log_formats import EventLogFormatError

Status = Literal["both", "left_only", "right_only", "left_endpoint_missing",
                 "right_endpoint_missing", "endpoint_type_conflict"]
STATUSES = ("both", "left_only", "right_only", "left_endpoint_missing",
            "right_endpoint_missing", "endpoint_type_conflict")


class Pin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    relationships_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    other_log_id: str = Field(pattern=r"^log_[0-9a-f]{16}$")
    same_namespace_declared: StrictBool
    left: Pin
    right: Pin
    source_type: str | None = Field(default=None, min_length=1, max_length=200)
    target_type: str | None = Field(default=None, min_length=1, max_length=200)
    qualifier: str | None = Field(default=None, max_length=80)
    status: Status | None = None
    offset: int = Field(default=0, ge=0, le=2_000_000)
    limit: int = Field(default=50, ge=1, le=200)


def _pin(conn, log_id):
    info = workspace._check_log(conn, log_id)
    if info["source_format"] not in {"ocel", "ocelxml"}:
        raise workspace.EventLogWorkspaceError("select a retained OCEL log")
    try:
        snap = relationships.snapshot(conn, log_id)
    except EventLogFormatError as exc:
        raise workspace.EventLogWorkspaceError(str(exc)) from None
    if conn.execute("""SELECT object_id FROM objects WHERE log_id=?
                       GROUP BY object_id HAVING count(*)>1 LIMIT 1""", [log_id]).fetchone():
        raise workspace.EventLogWorkspaceError("ambiguous object IDs in selected log")
    return {"log_id": log_id, "name": info["name"], **snap}


def pin(log_id):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            return _pin(conn, log_id)
        finally:
            conn.close()


def compare(log_id, spec):
    try:
        selection = Selection.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("invalid bounded relationship comparison settings") from None
    if not selection.same_namespace_declared or log_id == selection.other_log_id:
        raise workspace.EventLogWorkspaceError("choose two distinct logs and explicitly declare a common object-ID namespace")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            left, right = _pin(conn, log_id), _pin(conn, selection.other_log_id)
            for actual, expected in ((left, selection.left), (right, selection.right)):
                if any(actual[k] != v for k, v in expected.model_dump().items()):
                    raise workspace.EventLogWorkspaceError("retained snapshot changed; review both snapshot pins again")
            # Join literal IDs and qualifiers, then classify endpoint presence and type
            # conflicts separately. The union is never a merged event log.
            conn.execute("""CREATE TEMP TABLE relation_diff AS
                WITH l AS (SELECT source_object_id s,target_object_id t,qualifier q, true AS present
                           FROM object_relationships WHERE log_id=?),
                     r AS (SELECT source_object_id s,target_object_id t,qualifier q, true AS present
                           FROM object_relationships WHERE log_id=?),
                     u AS (SELECT coalesce(l.s,r.s) s,coalesce(l.t,r.t) t,coalesce(l.q,r.q) q,
                                  coalesce(l.present,false) in_left,coalesce(r.present,false) in_right
                           FROM l FULL OUTER JOIN r USING(s,t,q))
                SELECT u.*, ls.object_type left_source_type,lt.object_type left_target_type,
                       rs.object_type right_source_type,rt.object_type right_target_type,
                    CASE
                      WHEN (ls.object_type IS NOT NULL AND rs.object_type IS NOT NULL AND ls.object_type<>rs.object_type)
                        OR (lt.object_type IS NOT NULL AND rt.object_type IS NOT NULL AND lt.object_type<>rt.object_type)
                        THEN 'endpoint_type_conflict'
                      WHEN ls.object_id IS NULL OR lt.object_id IS NULL THEN 'left_endpoint_missing'
                      WHEN rs.object_id IS NULL OR rt.object_id IS NULL THEN 'right_endpoint_missing'
                      WHEN in_left AND in_right THEN 'both'
                      WHEN in_left THEN 'left_only'
                      ELSE 'right_only'
                    END AS status
                FROM u
                LEFT JOIN objects ls ON ls.log_id=? AND ls.object_id=u.s
                LEFT JOIN objects lt ON lt.log_id=? AND lt.object_id=u.t
                LEFT JOIN objects rs ON rs.log_id=? AND rs.object_id=u.s
                LEFT JOIN objects rt ON rt.log_id=? AND rt.object_id=u.t
                """, [log_id, selection.other_log_id, log_id, log_id,
                      selection.other_log_id, selection.other_log_id])
            where, params = [], []
            for field, lhs, rhs in (("source_type", "left_source_type", "right_source_type"),
                                    ("target_type", "left_target_type", "right_target_type")):
                value = getattr(selection, field)
                if value is not None:
                    where.append(f"({lhs}=? OR {rhs}=?)")
                    params.extend([value, value])
            if selection.qualifier is not None:
                where.append("q=?")
                params.append(selection.qualifier)
            predicate = " WHERE " + " AND ".join(where) if where else ""
            counts = dict.fromkeys(STATUSES, 0)
            counts.update(dict(conn.execute("SELECT status,count(*) FROM relation_diff"+predicate+
                                            " GROUP BY status", params).fetchall()))
            # Object-only diagnostics are independent of assertion filters.
            object_counts = dict(conn.execute("""WITH l AS (SELECT object_id,object_type FROM objects WHERE log_id=?),
                 r AS (SELECT object_id,object_type FROM objects WHERE log_id=?)
                 SELECT CASE WHEN l.object_id IS NULL THEN 'right_only'
                             WHEN r.object_id IS NULL THEN 'left_only'
                             WHEN l.object_type<>r.object_type THEN 'type_conflict'
                             ELSE 'both_same_type' END AS status,count(*)
                 FROM l FULL OUTER JOIN r USING(object_id) GROUP BY status""",
                 [log_id, selection.other_log_id]).fetchall())
            if selection.status is not None:
                predicate += (" AND " if predicate else " WHERE ")+"status=?"
                params.append(selection.status)
            total = conn.execute("SELECT count(*) FROM relation_diff"+predicate, params).fetchone()[0]
            page = conn.execute("""SELECT s,t,q,in_left,in_right,left_source_type,left_target_type,
                 right_source_type,right_target_type,status FROM relation_diff"""+predicate+
                 " ORDER BY s,t,q LIMIT ? OFFSET ?", [*params,selection.limit,selection.offset]).fetchall()
            fields = ("source_id","target_id","qualifier","in_left","in_right","left_source_type",
                      "left_target_type","right_source_type","right_target_type","status")
            rows = [dict(zip(fields,row)) for row in page]
            group = conn.execute("""SELECT coalesce(left_source_type,right_source_type),
                coalesce(left_target_type,right_target_type),q,status,count(*) FROM relation_diff"""+
                predicate+" GROUP BY 1,2,3,4 ORDER BY 5 DESC,1,2,3,4 LIMIT 101", params).fetchall()
        finally:
            conn.close()
    basis = selection.model_dump(exclude={"offset","limit"})
    return {
        "format": "openworkgraph.object-relationship-comparison.v1",
        "left": left, "right": right,
        "selection_sha256": sha256(json.dumps({"left_log_id":log_id,**basis},
                          sort_keys=True,ensure_ascii=True,separators=(",",":")).encode()).hexdigest(),
        "assertion_status_counts": counts, "filtered_union_count": sum(counts.values()),
        "whole_object_index_counts": {k:object_counts.get(k,0) for k in
                                      ("both_same_type","left_only","right_only","type_conflict")},
        "group_rows": [{"source_type":s,"target_type":t,"qualifier":q,"status":status,"count":n}
                       for s,t,q,status,n in group[:100]],
        "groups_truncated": len(group)>100,
        "group_basis": "left_type_preferred_when_endpoint_types_conflict",
        "page_count": total, "offset":selection.offset,
        "next_offset":selection.offset+len(rows) if selection.offset+len(rows)<total else None,
        "rows":rows, "same_namespace_user_declared":True,
        "source_identity_independently_verified":False,
        "historical_validity_verified":False, "source_completeness_verified":False,
        "actual_relationship_changes_verified":False, "case_membership_inferred":False,
        "execution_authorized":False,
    }
