"""Exact bounded synchronous event replay over selected object lifecycle models.

Each original event must be enabled for every linked modeled object and satisfy
its declared arcs. We keep all possible structural markings, never greedily
insert tokens. Components are connected by recorded event-object membership,
not inferred object relationships. This is not a general OC-Petri-net engine:
there is no unobserved object creation/transfer or binding choice.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Literal

from pydantic import Field, StrictInt, ValidationError
from shared.process_parallel import EdgeTokenModel
from . import event_log_object_process_model as ocpm
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.synchronized-object-event-replay.v1"
MAX_EVENTS = 30000
MAX_OBJECTS = 30000
MAX_LINKS = 150000
MAX_MARKINGS = 512
MAX_WORK = 2000000
MAX_COMPONENT_WORK = 200000
MAX_PAGE = 100
STATUSES = ("complete", "unfinished", "deviation", "unknown", "no_events")
EVENT_STATUSES = ("accepted", "deviation", "not_replayed", "unknown")


class Request(ocpm._Strict):
    model: ocpm.Model
    view: Literal["summary", "components", "events", "objects", "participants"] = "summary"
    component_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    event_id: ocpm.Label | None = None
    expected_review_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: Literal["all", "complete", "unfinished", "deviation", "unknown", "no_events",
                    "accepted", "not_replayed", "terminal_possible", "terminal_not_reachable"] = "all"
    offset: StrictInt = Field(default=0, ge=0, le=MAX_EVENTS+MAX_OBJECTS)
    limit: StrictInt = Field(default=50, ge=1, le=MAX_PAGE)


def _error(message):
    return workspace.EventLogWorkspaceError(message)


def _parse(value):
    try:
        req = Request.model_validate(value)
    except ValidationError:
        raise _error("Invalid synchronized object replay request") from None
    if req.view != "summary" and not req.expected_review_sha256:
        raise _error("Review the complete population before opening evidence")
    if req.view in ("events", "objects", "participants") and not req.component_id:
        raise _error("Select an exact reviewed component")
    if req.view in ("summary", "components") and req.component_id is not None:
        raise _error("Component selection is only valid for component evidence")
    if (req.view == "participants") != (req.event_id is not None):
        raise _error("Select an exact event only for participant evidence")
    allowed = {"participants": {"all"}, "summary": {"all"}, "components": {"all", *STATUSES},
               "events": {"all", *EVENT_STATUSES},
               "objects": {"all", "terminal_possible", "terminal_not_reachable", "unknown", "no_events"}}
    if req.status not in allowed[req.view] or (req.view == "summary" and req.offset):
        raise _error("Invalid evidence filter for this review")
    return req


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _stamp(ts):
    return ts.isoformat() + "Z"


@dataclass(frozen=True)
class Event:
    id: str
    activity: str
    timestamp: Any
    objects: tuple[str, ...]


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self):
        self.total = 0
        self.component = 0

    def begin(self):
        self.component = 0

    def tick(self):
        if self.total >= MAX_WORK or self.component >= MAX_COMPONENT_WORK:
            raise BudgetExceeded()
        self.total += 1
        self.component += 1


class Lifecycle:
    """All reachable markings of a single object's observed prefix.

    Silent closure uses the shared graph semantics, including paired AND/OR
    gateways. A budget overrun is unknown, never an empty marking set.
    """
    def __init__(self, model, labels):
        self.net = EdgeTokenModel(model)
        self.labels = labels

    def closure(self, markings, budget):
        seen = set(markings)
        queue = sorted(markings, key=lambda m: tuple(sorted(m)))
        while queue:
            current = queue.pop()
            budget.tick()
            for node, following in self.net.moves(current):
                budget.tick()
                if node.kind == "activity" or following in seen:
                    continue
                if len(seen) >= MAX_MARKINGS:
                    raise BudgetExceeded()
                seen.add(following)
                queue.append(following)
        return seen

    def advance(self, markings, activity, budget):
        label = self.labels.get(activity)
        if label is None:
            return set()
        result = set()
        for marking in sorted(self.closure(markings, budget), key=lambda m: tuple(sorted(m))):
            for node, following in self.net.moves(marking):
                budget.tick()
                if node.kind == "activity" and node.label == label:
                    result.add(following)
                    if len(result) > MAX_MARKINGS:
                        raise BudgetExceeded()
        return result

    def terminal(self, markings, budget):
        return self.net.done in self.closure(markings, budget)


class UnionFind:
    def __init__(self, keys):
        self.parent = {k:k for k in keys}

    def find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a, b):
        a,b = self.find(a),self.find(b)
        if a != b:
            self.parent[max(a,b)] = min(a,b)


def _components(objects, events):
    union = UnionFind(objects)
    for event in events:
        for obj in event.objects[1:]:
            union.union(event.objects[0], obj)
    groups = {}
    for obj in sorted(objects):
        key = ("object", union.find(obj))
        groups.setdefault(key, ([], []))[0].append(obj)
    for event in events:
        key = ("object", union.find(event.objects[0])) if event.objects else ("event",event.id)
        groups.setdefault(key, ([], []))[1].append(event)
    return list(groups.values())


def _event_shape(event, types, arcs, models):
    counts = Counter(types[o] for o in event.objects)
    issues = []
    for kind, variable in arcs.get(event.activity, {}).items():
        n = counts[kind]
        if n == 0:
            issues.append({"reason":"missing_required_object_type","object_type":kind,"objects":n})
        elif not variable and n != 1:
            issues.append({"reason":"multiple_objects_on_single_arc","object_type":kind,"objects":n})
    for kind, n in sorted(counts.items()):
        if kind not in arcs.get(event.activity, {}):
            issues.append({"reason":"linked_type_without_arc","object_type":kind,"objects":n})
        if event.activity not in models[kind].labels:
            issues.append({"reason":"activity_not_in_lifecycle","object_type":kind,"objects":n})
    # A model activity with no declared arcs and no selected links has no
    # binding at all. No fabricated zero-object firing.
    if not event.objects and not issues:
        issues.append({"reason":"no_modeled_object_binding","object_type":None,"objects":0})
    return issues


def _event_row(event, status, issues=(), blocked=()):
    return {"event_id":event.id,"activity":event.activity,"timestamp":_stamp(event.timestamp),
            "linked_modeled_object_count":len(event.objects),"status":status,
            "issues":list(issues),"blocked_object_count":len(blocked),
            "blocked_objects":list(blocked)[:20],"blocked_examples_truncated":len(blocked)>20}


def _replay_component(ids, events, types, models, arcs, budget):
    key = _digest({"objects":ids,"events":[e.id for e in events]})
    states = {o:{models[types[o]].net.initial} for o in ids}
    rows = []
    object_rows = []
    reason = None
    first_failure = None
    tied = set()
    seen_at = set()
    last = None
    for event in events:
        if event.timestamp != last:
            seen_at.clear()
            last = event.timestamp
        for o in event.objects:
            if o in seen_at:
                tied.add(o)
            seen_at.add(o)
    budget.begin()
    if not events:
        status = "no_events"
        reason = "no_retained_events_for_modeled_object"
    elif tied:
        # Distinct same-time events that share an object have no established
        # order. Refuse the whole connected component rather than order by ID.
        status = "unknown"
        reason = "shared_object_timestamp_tie"
        rows = [_event_row(e,"unknown") for e in events]
    else:
        status = "complete"
        for index,event in enumerate(events):
            shape = _event_shape(event,types,arcs,models)
            if shape:
                status,reason = "deviation","event_binding_shape"
                first_failure = event.id
                rows.append(_event_row(event,"deviation",shape))
                break
            updates = {}
            blocked = []
            try:
                for o in event.objects:
                    following = models[types[o]].advance(states[o],event.activity,budget)
                    if not following:
                        blocked.append({"object_id":o,"object_type":types[o],"reason":"activity_not_enabled"})
                    else:
                        updates[o] = following
            except BudgetExceeded:
                status,reason = "unknown","bounded_marking_search"
                rows.append(_event_row(event,"unknown"))
                break
            if blocked:
                status,reason = "deviation","atomic_event_not_enabled"
                first_failure = event.id
                rows.append(_event_row(event,"deviation",blocked=blocked))
                break
            # Atomic across every recorded participant. No object changes
            # state until all participating objects have an enabled firing.
            states.update(updates)
            rows.append(_event_row(event,"accepted"))
        if len(rows) < len(events):
            rows.extend(_event_row(e,"not_replayed") for e in events[len(rows):])
        if status == "complete":
            try:
                for o in ids:
                    terminal = models[types[o]].terminal(states[o],budget)
                    object_rows.append({"object_id":o,"object_type":types[o],
                                        "status":"terminal_possible" if terminal else "terminal_not_reachable",
                                        "reachable_markings":len(states[o])})
                if any(r["status"]=="terminal_not_reachable" for r in object_rows):
                    status,reason = "unfinished","terminal_not_reachable_after_observed_prefix"
            except BudgetExceeded:
                status,reason = "unknown","bounded_terminal_search"
                object_rows.clear()
    if not object_rows:
        object_rows = [{"object_id":o,"object_type":types[o],
                        "status":"no_events" if not events else "unknown",
                        "reachable_markings":None} for o in ids]
    summary = {"component_id":key,"status":status,"reason":reason,
               "objects":len(ids),"events":len(events),
               "first_timestamp":_stamp(events[0].timestamp) if events else None,
               "last_timestamp":_stamp(events[-1].timestamp) if events else None,
               "first_deviation_event_id":first_failure,
               "ambiguous_order_object_count":len(tied),
               "event_status_counts":{k:sum(r["status"]==k for r in rows) for k in EVENT_STATUSES},
               "object_status_counts":dict(sorted(Counter(r["status"] for r in object_rows).items())),
               "search_work":budget.component}
    return summary,rows,object_rows


def _load(conn, log_id, model):
    log = workspace._check_log(conn,log_id)
    if log["source_format"] not in ("ocel","ocelxml"):
        raise _error("Select a retained OCEL log")
    names = sorted(t.object_type for t in model.types)
    ocpm._validate_types(ocpm._present_types(conn,log_id),model)
    activity_names = sorted({a for t in model.types for a in ocpm._activity_names(t.model_dump())} |
                            {a.activity for a in model.arcs})
    for arc in model.arcs:
        if arc.object_type not in names:
            raise _error("Every replay arc must refer to a selected lifecycle type")
    conn.execute("""CREATE TEMP TABLE sync_objects AS SELECT object_id,object_type FROM objects
                 WHERE log_id=? AND list_contains(?,object_type)""",[log_id,names])
    count = conn.execute("SELECT count(*) FROM sync_objects").fetchone()[0]
    if count > MAX_OBJECTS:
        raise _error("Synchronized replay exceeds complete object budget")
    conn.execute("""CREATE TEMP TABLE sync_events AS SELECT e.event_id,e.activity,e.ts FROM events e
        WHERE e.log_id=? AND (list_contains(?,e.activity) OR EXISTS (
        SELECT 1 FROM event_objects l JOIN sync_objects o ON o.object_id=l.object_id
        WHERE l.log_id=e.log_id AND l.event_id=e.event_id))""",[log_id,activity_names])
    count = conn.execute("SELECT count(*) FROM sync_events").fetchone()[0]
    if count > MAX_EVENTS:
        raise _error("Synchronized replay exceeds complete event budget")
    # Count distinct participants before materialization, ignoring qualifier
    # multiplicity. This model does not declare qualifier-specific arcs.
    conn.execute("""CREATE TEMP TABLE sync_links AS SELECT DISTINCT l.event_id,l.object_id
        FROM event_objects l JOIN sync_objects o USING(object_id) JOIN sync_events e USING(event_id)
        WHERE l.log_id=?""",[log_id])
    links = conn.execute("SELECT count(*) FROM sync_links").fetchone()[0]
    if links > MAX_LINKS:
        raise _error("Synchronized replay exceeds complete membership budget")
    objects = dict(conn.execute("SELECT object_id,object_type FROM sync_objects ORDER BY object_id").fetchall())
    memberships = defaultdict(list)
    for event_id,object_id in conn.execute("SELECT event_id,object_id FROM sync_links ORDER BY event_id,object_id").fetchall():
        memberships[event_id].append(object_id)
    events = [Event(e,a,t,tuple(memberships[e])) for e,a,t in conn.execute(
        "SELECT event_id,activity,ts FROM sync_events ORDER BY ts,event_id").fetchall()]
    return log,objects,events,links


def review(log_id, value):
    req = _parse(value)
    models = {t.object_type:Lifecycle(*ocpm._validated(t)) for t in req.model.types}
    arcs = defaultdict(dict)
    for arc in req.model.arcs:
        arcs[arc.activity][arc.object_type] = arc.variable
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            log,objects,events,links = _load(conn,log_id,req.model)
        finally:
            conn.close()
    model_sha = _digest(req.model.model_dump(mode="json"))
    pin = _digest({"format":FORMAT,"log_id":log_id,"model_sha256":model_sha,
                   "objects":list(objects.items()),
                   "events":[[e.id,e.activity,_stamp(e.timestamp),e.objects] for e in events],
                   "limits":[MAX_EVENTS,MAX_OBJECTS,MAX_LINKS,MAX_MARKINGS,MAX_WORK,MAX_COMPONENT_WORK]})
    if req.expected_review_sha256 and req.expected_review_sha256 != pin:
        raise _error("Synchronized replay basis changed; refresh the complete review")
    budget = Budget()
    summaries = []
    chosen = None
    for ids,component_events in _components(objects,events):
        summary,rows,object_rows = _replay_component(ids,component_events,objects,models,arcs,budget)
        summaries.append(summary)
        if summary["component_id"] == req.component_id:
            chosen = (rows,object_rows,component_events)
    summaries.sort(key=lambda r:r["component_id"])
    counts = Counter(r["status"] for r in summaries)
    event_counts = Counter()
    object_counts = Counter()
    by_status = defaultdict(lambda:{"objects":0,"events":0})
    for s in summaries:
        event_counts.update(s["event_status_counts"])
        object_counts.update(s["object_status_counts"])
        by_status[s["status"]]["objects"] += s["objects"]
        by_status[s["status"]]["events"] += s["events"]
    if sum(event_counts.values()) != len(events) or sum(object_counts.values()) != len(objects):
        raise _error("Synchronized replay population accounting failed")
    if req.view in ("events","objects","participants"):
        if chosen is None:
            raise _error("Selected component is not in this reviewed population")
        if req.view == "participants":
            event = next((e for e in chosen[2] if e.id == req.event_id), None)
            if event is None:
                raise _error("Selected event is not in this reviewed component")
            evidence = [{"object_id":o,"object_type":objects[o],"status":"participant"} for o in event.objects]
        else:
            evidence = chosen[0 if req.view=="events" else 1]
    elif req.view == "components":
        evidence = summaries
    else:
        evidence = []
    filtered = [r for r in evidence if req.status=="all" or r["status"]==req.status]
    page = filtered[req.offset:req.offset+req.limit]
    return {"format":FORMAT,"log_id":log_id,"model_sha256":model_sha,"review_sha256":pin,
            "source_population":{"retained_events_total":log["events"],"retained_objects_total":log["objects"],
                                 "selected_events":len(events),"selected_objects":len(objects),
                                 "distinct_selected_memberships":links,
                                 "excluded_events":log["events"]-len(events),
                                 "excluded_objects":log["objects"]-len(objects)},
            "component_count":len(summaries),"component_status_counts":{k:counts[k] for k in STATUSES},
            "population_by_component_status":{k:by_status[k] for k in STATUSES},
            "event_status_counts":{k:event_counts[k] for k in EVENT_STATUSES},
            "object_status_counts":dict(sorted(object_counts.items())),
            "search_work":budget.total,"all_components_classified":True,
            "all_selected_events_accepted":bool(events) and event_counts["accepted"]==len(events),
            "all_selected_components_complete":bool(summaries) and counts["complete"]==len(summaries),
            "page":{"view":req.view,"status":req.status,"component_id":req.component_id,
                    "event_id":req.event_id,"total":len(filtered),"offset":req.offset,"rows":page,
                    "next_offset":req.offset+len(page) if req.offset+len(page)<len(filtered) else None},
            "binding_semantics":"atomic_observed_event_over_selected_object_lifecycle_markings",
            "general_ocpn_replay":False,"qualifier_specific_arcs_evaluated":False,
            "supplied_object_relationships_evaluated":False,"hidden_activity_projection_applied":False,
            "business_rules_evaluated":False,"execution_authorized":False,"business_outcome_verified":False,
            "interpretation":[
                "Uses raw retained OCEL events, selected object types and distinct event-object participants. Unselected object types, qualifiers and supplied object-to-object edges do not define additional binding constraints.",
                "An original event advances every recorded modeled participant atomically, retaining every reachable structural marking within bounded work. A failed component stops at its first demonstrated deviation; later events are not replayed.",
                "Events with a modeled activity but no modeled participants are checked for missing bindings. Registered isolated objects are classified separately.",
                "Same-timestamp events sharing an object make the entire connected component unknown. Unrelated simultaneous events do not need an arbitrary order. A budget exhaustion is also unknown.",
                "Unfinished is an accepted prefix for which at least one lifecycle cannot silently reach its terminal marking. It does not prove a business failure or distinguish an incomplete log from ongoing work.",
                "This synchronized projection model is not a general object-centric Petri net with object creation, arbitrary place bindings, relationship continuity or business policy semantics."
            ]}
