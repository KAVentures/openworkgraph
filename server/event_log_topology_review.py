"""Bounded read-only topology review of one exact retained OCEL snapshot."""
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from shared import ocel_topology as core
from shared import ocel_directed_topology as directed_core
from shared.event_log_formats import _exact_key
from . import event_log_relationship_comparison as comparison
from . import event_log_object_relationships as relationships
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.ocel-topology-review.v1"
MAX_OBJECTS = 20000
MAX_ASSERTIONS = 100000


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot: comparison.Pin
    confirm: Literal[True]
    qualifier: str | None = Field(default=None,max_length=80)
    view: Literal["components","objects","bridges","directed_components","directed_members"] = "components"
    offset: int = Field(default=0,ge=0,le=MAX_OBJECTS)
    limit: int = Field(default=50,ge=1,le=100)
    expected_review_sha256: str | None = Field(default=None,pattern=r"^[a-f0-9]{64}$")

    @field_validator("qualifier")
    @classmethod
    def literal(cls,value):
        if value is not None:
            _exact_key(value,80)
        return value

    @field_validator("confirm",mode="before")
    @classmethod
    def explicit(cls,value):
        if value is not True:
            raise ValueError("Confirm the exact retained snapshot")
        return value


def review(log_id, payload):
    try:
        spec = Review.model_validate(payload)
    except (ValidationError,ValueError):
        raise workspace.EventLogWorkspaceError("Review an exact OCEL snapshot with explicit confirmation and bounded page settings") from None
    if spec.offset and not spec.expected_review_sha256:
        raise workspace.EventLogWorkspaceError("Review the first topology page before paging")
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            info=workspace._check_log(conn,log_id)
            if info["source_format"] not in {"ocel","ocelxml"}:
                raise workspace.EventLogWorkspaceError("Select a retained OCEL log")
            object_count=conn.execute("SELECT count(*) FROM objects WHERE log_id=?",[log_id]).fetchone()[0]
            assertion_count=relationships.count(conn,log_id)
            if object_count>MAX_OBJECTS or assertion_count>MAX_ASSERTIONS:
                raise workspace.EventLogWorkspaceError("Complete topology review exceeds the object or assertion budget; use a smaller retained log")
            pin=comparison._pin(conn,log_id)
            if spec.snapshot.model_dump()!={k:pin[k] for k in spec.snapshot.model_dump()}:
                raise workspace.EventLogWorkspaceError("Retained relationship snapshot changed; inspect it again")
            review_pin=sha256(json.dumps([FORMAT,log_id,spec.snapshot.model_dump(),spec.qualifier,spec.view],
                sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
            if spec.expected_review_sha256 and spec.expected_review_sha256!=review_pin:
                raise workspace.EventLogWorkspaceError("Topology review settings changed; restart at page one")
            objects=conn.execute("SELECT object_id,object_type FROM objects WHERE log_id=? ORDER BY 1",[log_id]).fetchall()
            sql="SELECT source_object_id,target_object_id,qualifier FROM object_relationships WHERE log_id=?"
            params=[log_id]
            if spec.qualifier is not None:
                sql+=" AND qualifier=?"
                params.append(spec.qualifier)
            assertions=conn.execute(sql+" ORDER BY 1,2,3",params).fetchall()
        finally:
            conn.close()
    directed=spec.view in {"directed_components","directed_members"}
    try:
        graph=(directed_core if directed else core).analyze(objects,assertions)
    except ValueError as exc:
        raise workspace.EventLogWorkspaceError(str(exc)) from None
    rows=graph[spec.view]
    page=rows[spec.offset:spec.offset+spec.limit]
    return {"format":FORMAT,"review_sha256":review_pin,"snapshot":pin,
        "qualifier":spec.qualifier,"view":spec.view,"counts":graph["counts"],
        "offset":spec.offset,"matching_rows":len(rows),"rows":page,
        "next_offset":spec.offset+len(page) if page and spec.offset+len(page)<len(rows) else None,
        "complete_selected_retained_graph":True,"undirected_projection":not directed,
        "all_retained_objects_in_scope":True,"independently_verified":False,
        "case_membership_inferred":False,"historical_validity_known":False,
        "operational_impact_verified":False,"source_reads_performed":False,"execution_authorized":False,
        "interpretation":([
            "Direction is preserved as supplied. A strong component contains mutually reachable objects. Multiple qualifiers on the same ordered endpoints collapse into one directed pair, but literal assertion counts remain separate.",
            "A cyclic component contains multiple mutually reachable objects or a literal self loop. Source and sink describe the acyclic graph of strong components, not actual business origins or outcomes. Isolated objects are both source and sink.",
            "All retained objects remain in scope, including isolates. Incoming and outgoing neighbor counts exclude self loops. The static graph does not establish temporal order, causality, case identity, source completeness or operational impact.",
            "The exact retained snapshot is pinned. The report is read-only and no provider or business action occurs."] if directed else [
            "All retained objects remain in the population, including isolates and objects with no matching assertions. Directed assertions between distinct endpoints collapse into one undirected projected pair. Self assertions are counted but do not connect objects.",
            "A bridge pair requires removing every selected assertion between its endpoints, in either direction. A single-assertion bridge has exactly one such assertion. An articulation object removes that object and its incident pairs; disconnected surviving-pair counts exclude the removed object.",
            "Disconnected pair counts are unordered formerly connected object pairs separated by one hypothetical removal, not operational loss, causal impact or a recommendation to change a relationship. No real-world relationship, valid time, case identity or source completeness is verified.",
            "The exact retained snapshot is pinned before reading the complete selected graph. The report is read-only and no provider or business action occurs."])}
