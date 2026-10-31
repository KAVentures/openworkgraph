"""Object-centric process models: discovery and conformance over saved OCEL logs (#370 R24).

An object-centric process model, in the spirit of object-centric Petri nets (van der Aalst & Berti, 2020),
has one lifecycle model per object type and *arcs* that say which object types each activity involves:

- a ``single`` arc means every event of the activity involves exactly one object of the type;
- a ``variable`` arc means it involves at least one, possibly many (one Place order event, many items).

Discovery runs the existing bounded inductive discovery on each type's own lifecycles (the log flattened onto
that type, one type at a time) and measures each arc from the recorded event-object links.

Conformance checks a model, discovered or edited, in two parts that never flatten the log onto one case notion:

- **lifecycles:** each object's events, in time then event-ID order, are token-replayed on its type's model.
  Events of activities the model does not contain count as unmodelled (missing tokens);
- **arcs:** each event of a modelled activity is checked against its arcs. A missing object of a required type,
  more than one object on a single arc, and an involved type the activity has no arc for are counted
  separately, with examples.

This is a projection-based check: lifecycles are replayed per type, and how objects of different types meet is
checked through the arcs. It is not a full binding-based replay of an object-centric Petri net, so it does not
check that the same objects flow together through several shared activities (object rules check that). Results
describe the recorded links of the imported log, not verified business relationships or compliance.
"""
from __future__ import annotations

from collections import Counter
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, model_validator

from shared.process_model import ProcessModel
from shared.process_token_replay import replay_paths

from . import event_log_model_discovery as discovery
from . import event_log_workspace as workspace

VERSION = "openworkgraph.object-centric-model.v1"
MAX_TYPES = 8
MAX_ARCS = 400
MAX_VARIANTS = 1000
MAX_TRACE_LENGTH = 300
MAX_REPLAY_EVENTS = 300_000
MAX_DISPLAY_VARIANTS = 10
MAX_EXAMPLES = 20
_UNMODELLED = "\x00"          # prefix that no model label can carry, so a raw name never matches a label
Label = Annotated[str, Field(min_length=1, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TypeModel(_Strict):
    object_type: Label
    process_model: dict[str, Any]
    # Model labels whose log activity differs (labels are normalized); others name their activity exactly.
    activities: dict[Annotated[str, Field(max_length=64)], Label] = Field(default_factory=dict, max_length=40)


class Arc(_Strict):
    activity: Label
    object_type: Label
    variable: StrictBool


class Model(_Strict):
    version: Literal["openworkgraph.object-centric-model.v1"]
    types: list[TypeModel] = Field(min_length=1, max_length=MAX_TYPES)
    arcs: list[Arc] = Field(default_factory=list, max_length=MAX_ARCS)

    @model_validator(mode="after")
    def _unique(self):
        names = [t.object_type for t in self.types]
        if len(set(names)) != len(names):
            raise ValueError("each object type may have one model")
        keys = [(a.activity, a.object_type) for a in self.arcs]
        if len(set(keys)) != len(keys):
            raise ValueError("each activity and object type may have one arc")
        for t in self.types:
            if len(set(t.activities.values())) != len(t.activities):
                raise ValueError(f"two labels of the {t.object_type} model name the same activity")
        return self


class CheckSpec(_Strict):
    model: Model
    max_variants: StrictInt = Field(default=200, ge=1, le=MAX_VARIANTS)


class DiscoverSpec(_Strict):
    object_types: list[Label] | None = Field(default=None, min_length=1, max_length=MAX_TYPES)
    noise: float = Field(default=0.2, ge=0, lt=1)
    activities: float = Field(default=1.0, gt=0, le=1)


def _parse(cls, spec: Any):
    try:
        return cls.model_validate(spec)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise workspace.EventLogWorkspaceError(f"object-centric model: {where + ': ' if where else ''}{first['msg']}") from None


# --- discovery ---------------------------------------------------------------

def _leaves(tree) -> list[str]:
    """Activities of a discovered tree in the order the model numbers its activity nodes."""
    if tree["op"] == "activity":
        return [tree["activity"]]
    return [a for child in tree.get("children", []) for a in _leaves(child)]


def _labels(tree, model) -> dict[str, str] | None:
    """Model label → log activity, or None when two activities share a normalized label."""
    nodes = [n for n in model["activities"] if n["kind"] == "activity"]
    leaves = _leaves(tree)
    if len(nodes) != len(leaves):
        return None
    mapping: dict[str, str] = {}
    for node, activity in zip(nodes, leaves):
        expected = discovery._label(activity, "Activity " + node["id"].split("_")[1])
        if node["label"] != expected or mapping.get(node["label"], activity) != activity:
            return None
        mapping[node["label"]] = activity
    return mapping


def _arc_stats(log_id: str, types: list[str]) -> list[dict[str, Any]]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("""SELECT activity, object_type, count(*), min(n), max(n), sum(n) FROM (
                  SELECT e.event_id, e.activity, o.object_type, count(DISTINCT eo.object_id) AS n
                  FROM events e JOIN event_objects eo ON eo.log_id = e.log_id AND eo.event_id = e.event_id
                  JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
                  WHERE e.log_id = ? AND list_contains(?, o.object_type) GROUP BY 1, 2, 3)
                GROUP BY 1, 2 ORDER BY 1, 2""", [log_id, types]).fetchall()
            totals = dict(conn.execute("SELECT activity, count(*) FROM events WHERE log_id = ? GROUP BY 1",
                                       [log_id]).fetchall())
        finally:
            conn.close()
    return [{"activity": a, "object_type": t, "variable": hi > 1, "activity_events": totals[a],
             "events_with_type": n, "share_of_activity_events": round(n / totals[a], 6),
             "objects_per_event": {"min": lo, "max": hi, "mean": round(s / n, 6)}} for a, t, n, lo, hi, s in rows]


def discover(log_id: str, spec: Any = None) -> dict[str, Any]:
    chosen = _parse(DiscoverSpec, spec or {})
    present = [t["object_type"] for t in workspace.object_types(log_id)["object_types"]]
    if chosen.object_types:
        unknown = sorted(set(chosen.object_types) - set(present))
        if unknown:
            raise workspace.EventLogWorkspaceError(f"unknown object type for this log: {unknown[0]}")
        types = list(dict.fromkeys(chosen.object_types))
    else:
        types = present[:MAX_TYPES]
    models, skipped = [], []
    for object_type in types:
        try:
            found = discovery.discover(log_id, object_type, noise=chosen.noise, activities=chosen.activities)
        except workspace.EventLogWorkspaceError as error:
            skipped.append({"object_type": object_type, "reason": str(error)})
            continue
        mapping = _labels(found["tree"], found["process_model"])
        if mapping is None:
            skipped.append({"object_type": object_type, "reason": "two activities share a model label after "
                                                                  "normalization, so their objects cannot be replayed"})
            continue
        models.append({"object_type": object_type, "process_model": found["process_model"],
                       "activities": {label: a for label, a in mapping.items() if label != a},
                       "quality": found["quality"], "simplifications": found["simplifications"]})
    if not models:
        raise workspace.EventLogWorkspaceError("no object type could be discovered: " +
                                               "; ".join(f"{s['object_type']}: {s['reason']}" for s in skipped))
    modelled = {(m["object_type"], a) for m in models for a in _activity_names(m)}
    arcs = [r for r in _arc_stats(log_id, [m["object_type"] for m in models])
            if (r["object_type"], r["activity"]) in modelled]
    info = workspace.object_types(log_id)["log"]
    return {
        "log": info,
        "model": {"version": VERSION,
                  "types": [{k: m[k] for k in ("object_type", "process_model", "activities")} for m in models],
                  "arcs": [{k: r[k] for k in ("activity", "object_type", "variable")} for r in arcs]},
        "type_quality": [{k: m[k] for k in ("object_type", "quality", "simplifications")} for m in models],
        "arc_measurements": arcs,
        "types_not_discovered": skipped,
        "types_omitted_over_limit": present[MAX_TYPES:] if not chosen.object_types else [],
        "settings": {"noise": chosen.noise, "activities": chosen.activities},
        "method": "per_object_type_bounded_inductive_discovery_with_measured_arcs",
        "interpretation": ("A discovered draft for review: one lifecycle model per object type, discovered from that "
                           "type's objects only, and arcs measured from the recorded event-object links. A variable "
                           "arc means some event involved several objects of the type. Not a verified workflow."),
        **workspace.DESCRIPTIVE_ONLY,
    }


# --- conformance ---------------------------------------------------------------

def _activity_names(type_model: dict[str, Any]) -> list[str]:
    mapping = type_model.get("activities") or {}
    return [mapping.get(n["label"], n["label"]) for n in type_model["process_model"]["activities"]
            if n["kind"] == "activity"]


def _validated(type_model: TypeModel) -> tuple[ProcessModel, dict[str, str]]:
    # Discovered drafts leave the business goal to the reviewer; replay checks structure only.
    structure = {**type_model.process_model, "goal": type_model.process_model.get("goal") or "Object lifecycle"}
    try:
        model = ProcessModel.model_validate(structure)
    except (ValidationError, TypeError):
        raise workspace.EventLogWorkspaceError(
            f"the {type_model.object_type} model is not a valid process model; review its structure") from None
    labels = {a.label for a in model.activities if a.kind == "activity"}
    stray = sorted(set(type_model.activities) - labels)
    if stray:
        raise workspace.EventLogWorkspaceError(f"the {type_model.object_type} model has no activity labelled {stray[0]}")
    names = {label: type_model.activities.get(label, label) for label in labels}
    if len(set(names.values())) != len(names):
        raise workspace.EventLogWorkspaceError(f"two labels of the {type_model.object_type} model name the same activity")
    return model, {activity: label for label, activity in names.items()}


def _lifecycles(log_id: str, type_model: TypeModel, max_variants: int) -> dict[str, Any]:
    model, label_of = _validated(type_model)
    shown = lambda name: name[1:] if name.startswith(_UNMODELLED) else type_model.activities.get(name, name)

    def run(conn):
        total, long_objects = conn.execute("""SELECT count(*), count(*) FILTER (WHERE n > ?) FROM (
            SELECT count(*) AS n FROM ce GROUP BY case_id)""", [MAX_TRACE_LENGTH]).fetchone()
        rows = conn.execute("""SELECT path, count(*) FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id HAVING count(*) <= ?)
            GROUP BY path ORDER BY 2 DESC, path LIMIT ?""", [MAX_TRACE_LENGTH, max_variants]).fetchall()
        paths = [(tuple(label_of.get(a, _UNMODELLED + a) for a in path), n) for path, n in rows]
        replay = replay_paths(paths, model, max_events=MAX_REPLAY_EVENTS)
        replayed = replay["replayed_cases"]
        unknown = {"trace_length_limit": long_objects,
                   "variant_limit": total - long_objects - sum(n for _, n in rows),
                   "replay_budget": sum(n for _, n in rows) - replayed}
        relabel = lambda counts: {shown(k): v for k, v in counts.items()}
        return {"object_type": type_model.object_type, "objects": total, "replayed_objects": replayed,
                "fitting_objects": replay["fitting_cases"], "fitting_object_share": replay["fitting_case_share"],
                "fitting_object_share_of_all_lower_bound": replay["fitting_cases"] / total if total else None,
                "token_fitness": replay["token_fitness"], "tokens": replay["tokens"],
                "missing_tokens_by_activity": relabel(replay["missing_tokens_by_activity"]),
                "remaining_tokens_by_activity": relabel(replay["remaining_tokens_by_activity"]),
                "unmodelled_activities": relabel(replay["unmodelled_activities"]),
                "variants": [{**v, "path": [shown(a) for a in v["path"]]} for v in replay["variants"][:MAX_DISPLAY_VARIANTS]],
                "unknown_objects": total - replayed, "unknown_object_reasons": unknown,
                "complete": replayed == total}
    result = workspace._query(log_id, type_model.object_type, run)
    return {k: v for k, v in result.items() if k not in ("log", "case_notion", *workspace.DESCRIPTIVE_ONLY)}


def _arc_check(log_id: str, parsed: Model) -> dict[str, Any]:
    """Events of modelled activities against their arcs, over the modelled object types."""
    types = [t.object_type for t in parsed.types]
    modelled = sorted({a for t in parsed.types for a in _activity_names(t.model_dump())} |
                      {a.activity for a in parsed.arcs})
    arcs = [(a.activity, a.object_type, a.variable) for a in parsed.arcs]
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            conn.execute("""CREATE OR REPLACE TEMP TABLE arc_links AS
                SELECT e.event_id, e.activity, e.ts, o.object_type, count(DISTINCT eo.object_id) AS n
                FROM events e LEFT JOIN event_objects eo ON eo.log_id = e.log_id AND eo.event_id = e.event_id
                LEFT JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
                    AND list_contains(?, o.object_type)
                WHERE e.log_id = ? AND list_contains(?, e.activity) GROUP BY 1, 2, 3, 4""", [types, log_id, modelled])
            conn.execute("CREATE OR REPLACE TEMP TABLE arc_model (activity VARCHAR, object_type VARCHAR, variable BOOLEAN)")
            if arcs:
                conn.executemany("INSERT INTO arc_model VALUES (?, ?, ?)", arcs)
            conn.execute("""CREATE OR REPLACE TEMP TABLE arc_issues AS
                SELECT e.event_id, e.activity, e.ts, m.object_type, 'missing_object' AS issue, 0 AS n
                FROM (SELECT DISTINCT event_id, activity, ts FROM arc_links) e JOIN arc_model m USING (activity)
                WHERE NOT EXISTS (SELECT 1 FROM arc_links l WHERE l.event_id = e.event_id AND l.object_type = m.object_type)
                UNION ALL
                SELECT l.event_id, l.activity, l.ts, l.object_type, 'several_objects_on_single_arc', l.n
                FROM arc_links l JOIN arc_model m USING (activity, object_type) WHERE NOT m.variable AND l.n > 1
                UNION ALL
                SELECT l.event_id, l.activity, l.ts, l.object_type, 'type_without_arc', l.n
                FROM arc_links l WHERE l.object_type IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM arc_model m WHERE m.activity = l.activity AND m.object_type = l.object_type)""")
            events = conn.execute("SELECT count(DISTINCT event_id) FROM arc_links").fetchone()[0]
            failing = conn.execute("SELECT count(DISTINCT event_id) FROM arc_issues").fetchone()[0]
            by_arc = conn.execute("""SELECT activity, object_type, issue, count(*) FROM arc_issues
                GROUP BY 1, 2, 3 ORDER BY 4 DESC, 1, 2, 3""").fetchall()
            examples = conn.execute(f"""SELECT event_id, activity, object_type, issue, n FROM arc_issues
                ORDER BY ts, event_id, object_type, issue LIMIT {MAX_EXAMPLES}""").fetchall()
            activity_events = dict(conn.execute("""SELECT activity, count(DISTINCT event_id) FROM arc_links
                GROUP BY 1""").fetchall())
        finally:
            conn.close()
    issues = Counter()
    for _, _, issue, n in by_arc:
        issues[issue] += n
    return {"events_checked": events, "fitting_events": events - failing,
            "fitting_event_share": (events - failing) / events if events else None,
            "issues": {k: issues.get(k, 0) for k in ("missing_object", "several_objects_on_single_arc", "type_without_arc")},
            "issues_by_arc": [{"activity": a, "object_type": t, "issue": i, "events": n} for a, t, i, n in by_arc],
            "examples": [{"event_id": e, "activity": a, "object_type": t, "issue": i, "objects": n}
                         for e, a, t, i, n in examples],
            "modelled_activities_absent_from_log": [a for a in modelled if a not in activity_events]}


def check(log_id: str, spec: Any) -> dict[str, Any]:
    parsed = _parse(CheckSpec, spec)
    present = {t["object_type"] for t in workspace.object_types(log_id)["object_types"]}
    for name in [t.object_type for t in parsed.model.types] + [a.object_type for a in parsed.model.arcs]:
        if name not in present:
            raise workspace.EventLogWorkspaceError(f"unknown object type for this log: {name}")
    lifecycles = [_lifecycles(log_id, t, parsed.max_variants) for t in parsed.model.types]
    arcs = _arc_check(log_id, parsed.model)
    objects = sum(t["objects"] for t in lifecycles)
    replayed = sum(t["replayed_objects"] for t in lifecycles)
    fitting = sum(t["fitting_objects"] for t in lifecycles)
    return {
        "log": workspace.object_types(log_id)["log"],
        "lifecycles": lifecycles,
        "arcs": arcs,
        "summary": {"objects": objects, "replayed_objects": replayed, "fitting_objects": fitting,
                    "fitting_object_share": fitting / replayed if replayed else None,
                    "events_checked": arcs["events_checked"], "fitting_events": arcs["fitting_events"],
                    "complete": replayed == objects},
        "ordering_basis": "timestamp_then_event_id",
        "method": "projection_based_object_centric_replay",
        "binding_based_replay": False,
        "interpretation": ("Each object's lifecycle is token-replayed on its type's model, and each event of a modelled "
                           "activity is checked against its arcs. This projection does not check that the same objects "
                           "move together through several shared activities; object rules check that. Results "
                           "describe the recorded links of this log, not verified business relationships, audit "
                           "evidence or compliance; a deviation may reflect missing events or links."),
        "business_rules_evaluated": False, "policy_compliance_verified": False,
        **workspace.DESCRIPTIVE_ONLY,
    }


__all__ = ["VERSION", "check", "discover"]
