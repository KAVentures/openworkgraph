"""R12: reviewed, read-only batch maintenance of pinned repository maps.

A report is a proposed draft, not a saved revision or model approval.
The existing hierarchy save transaction remains authoritative.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from shared.process_hierarchy import Digest, Hierarchy, ID, Strict, content_digest
from .process_hierarchies import Error, MAX_REVISIONS


class RefreshRequest(Strict):
    map_revision: Annotated[StrictInt, Field(ge=1, le=MAX_REVISIONS)]
    expected_map_sha256: Digest
    node_ids: Annotated[list[ID], Field(min_length=1, max_length=100)]
    expected_report_sha256: Digest | None = None

    @model_validator(mode="after")
    def unique_nodes(self):
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError("Select each occurrence at most once")
        return self


def _reference(record):
    return {k: record[k] for k in ("workflow_id", "revision", "content_sha256")}


def _key(reference):
    return (reference["workflow_id"], reference["revision"], reference["content_sha256"])


def _paths(nodes):
    index = {n["id"]: n for n in nodes}
    result = {}
    for node in nodes:
        path = [node["id"]]
        parent = node["parent_id"]
        while parent is not None:
            path.append(parent)
            parent = index[parent]["parent_id"]
        result[node["id"]] = list(reversed(path))
    return result


def _latest_reference(maps, c, p, workflow_id):
    """Validate the exact current revision via the existing resolution contract.

    Do not return titles, hashes, or model fields from a failed resolution.
    """
    try:
        record = maps.store._record(c, p, workflow_id)
        ref = _reference(record)
        if ref["workflow_id"] != workflow_id:
            raise ValueError("Reference identity changed")
        info = maps.resolve(c, p, ref)
    except Error as exc:
        if exc.status != 404:
            raise
        return None, "unavailable"
    except (ValueError, KeyError, TypeError):
        return None, "integrity_failed"
    if not info["available"]:
        return None, info["state"]
    if info["archived"]:
        return None, "archived"
    if not info["current"]:
        return None, "revision_changed"
    return {"reference": ref, "title": info["title"],
            "independent_model_review": info["independent_model_review"]}, "current"


def build(maps, c, p, item, meta, latest_map, selected):
    hierarchy = item["hierarchy"]
    nodes = hierarchy["nodes"]
    index = {n["id"]: n for n in nodes}
    if not selected <= index.keys():
        raise Error("Choose exact node IDs in the selected saved map", 422)
    paths = _paths(nodes)
    candidate = deepcopy(hierarchy)
    candidate_index = {n["id"]: n for n in candidate["nodes"]}
    existing, latest = {}, {}
    rows = []
    blockers = []
    changed = set()

    def block(node_id, reason):
        blockers.append({"node_id": node_id, "reason": reason})

    role = maps.store._role(c, p, index["root"]["process"]["workflow_id"], meta)
    can_edit = (not meta["archived"] and role in {"owner", "editor", "admin"}
                and "org:process:write" in p.scopes)
    if not can_edit:
        block("root", "current_root_editor_required")
    if item["revision"] != latest_map:
        block("root", "historical_map_is_read_only")
    if latest_map >= MAX_REVISIONS:
        block("root", "map_revision_capacity_reached")

    for node in nodes:
        nid = node["id"]
        before = node["process"]
        wid = before["workflow_id"]
        k = _key(before)
        if k not in existing:
            existing[k] = maps.resolve(c, p, before)
        old = existing[k]
        if wid not in latest:
            latest[wid] = _latest_reference(maps, c, p, wid)
        newest, latest_state = latest[wid]
        after = before
        if nid in selected and newest is not None:
            after = newest["reference"]
            candidate_index[nid]["process"] = deepcopy(after)
            if after != before:
                changed.add(nid)
        elif nid in selected:
            block(nid, "selected_latest_" + latest_state)

        # A corrupt original may be repaired by an explicit selected refresh
        # only if its replacement independently resolves. Other bad references
        # must not be hidden by a preview of a different branch.
        if after == before:
            if not old["available"] or old.get("archived"):
                block(nid, "retained_reference_" + old["state"])
            if nid == "root" and not old.get("current"):
                block(nid, "root_latest_reference_required")
        rows.append({
            "node_id": nid, "node_path": paths[nid], "selected": nid in selected,
            "before": before, "before_state": old["state"],
            "before_review": old.get("independent_model_review") if old["available"] else None,
            "latest_state": latest_state,
            "latest": newest,
            "proposed": after,
            "reference_changed": after != before,
            "proposed_review": (newest["independent_model_review"]
                                if newest is not None and after == newest["reference"]
                                else old.get("independent_model_review") if old["available"] else None),
        })

    # Validate that the proposal preserves the exact tree and schema. A change
    # to one occurrence is not automatically applied to sibling occurrences.
    candidate = Hierarchy.model_validate(candidate).model_dump(mode="json")
    impacts = []
    for nid in sorted(changed):
        descendants = [n for n, path in paths.items() if nid in path[:-1]]
        ancestors = paths[nid][:-1]
        impacts.append({"node_id": nid, "ancestor_node_ids": ancestors,
                        "descendant_node_ids": sorted(descendants),
                        "descendant_count": len(descendants)})
    ancestors = {a for row in impacts for a in row["ancestor_node_ids"]}
    descendants = {d for row in impacts for d in row["descendant_node_ids"]}
    affected = changed | ancestors | descendants

    by_workflow = defaultdict(list)
    for row in rows:
        by_workflow[row["before"]["workflow_id"]].append(row)
    reuse = []
    for wid, group in sorted(by_workflow.items()):
        if len(group) < 2:
            continue
        before = {_key(r["before"]) for r in group}
        after = {_key(r["proposed"]) for r in group}
        reuse.append({
            "workflow_id": wid, "occurrence_node_ids": [r["node_id"] for r in group],
            "selected_node_ids": [r["node_id"] for r in group if r["selected"]],
            "changed_node_ids": [r["node_id"] for r in group if r["reference_changed"]],
            "distinct_pins_before": len(before), "distinct_pins_after": len(after),
            "mixed_pins_after": len(after) > 1,
        })
    warnings = []
    if any(row["mixed_pins_after"] for row in reuse):
        warnings.append("reused_process_has_multiple_proposed_pins")
    if any(r["reference_changed"] and r["proposed_review"] != "reviewed_design" for r in rows):
        warnings.append("changed_reference_lacks_current_independent_design_review")
    if any(not r["selected"] and r["before_state"] == "revision_changed" for r in rows):
        warnings.append("unselected_outdated_references_remain")
    if not changed:
        warnings.append("no_reference_changes")
    counts = {
        "occurrences": len(rows), "selected_occurrences": len(selected),
        "changed_occurrences": len(changed), "affected_occurrences": len(affected),
        "ancestor_occurrences": len(ancestors), "descendant_occurrences": len(descendants),
        "reused_workflows": len(reuse), "mixed_pin_workflows_after": sum(r["mixed_pins_after"] for r in reuse),
        "proposed_review_counts": dict(sorted(Counter(
            r["proposed_review"] or "unavailable" for r in rows).items())),
        "blockers": len(blockers),
    }
    return {
        "format": "openworkgraph.repository-map-reference-refresh.v1",
        "root_workflow_id": index["root"]["process"]["workflow_id"],
        "map_revision": item["revision"], "map_sha256": item["content_sha256"],
        "latest_map_revision": latest_map, "root_access_revision": meta["access_revision"],
        "selected_node_ids": sorted(selected), "rows": rows, "counts": counts,
        "changed_node_ids": sorted(changed), "affected_node_ids": sorted(affected),
        "branch_impacts": impacts, "reused_processes": reuse,
        "warnings": warnings, "blockers": blockers,
        "candidate_hierarchy": candidate, "candidate_hierarchy_sha256": content_digest(candidate),
        "can_stage": bool(changed) and not blockers,
        "persisted": False, "model_reviews_propagated": False,
        "execution_authorized": False, "benefit_rollup_included": False,
        "interpretation": [
            "The proposal changes selected exact reference occurrences only. Ancestors and descendants are map navigation context, not proven operational dependencies.",
            "A repeated process may intentionally use different revisions across branches. No occurrence is implicitly refreshed.",
            "Unavailable or archived references and a stale root block staging unless repaired in the reviewed proposal. Independent model review is shown separately; it is not inherited.",
            "Staging rechecks the same report digest. It does not save anything. Explicit sharing confirmation and the existing save transaction are still required; child pins may become older after review.",
        ],
    }


def review(maps, p, workflow_id, request: RefreshRequest):
    with maps.reading(p) as c:
        meta = maps.store._meta(c, p, workflow_id)
        item = maps.get(c, p, workflow_id, request.map_revision)
        if item is None:
            raise Error("Selected repository map revision not found", 404)
        if item["content_sha256"] != request.expected_map_sha256:
            raise Error("Repository map digest changed; reopen the selected revision", 409)
        report = build(maps, c, p, item, meta, maps.latest(c, p, workflow_id),
                       set(request.node_ids))
        report["report_sha256"] = content_digest(report)
        if request.expected_report_sha256 and request.expected_report_sha256 != report["report_sha256"]:
            raise Error("Reference refresh basis changed; review again before staging", 409)
    maps.db.audit(organization_id=p.organization_id, principal_id=p.audit_id,
                  action="process.hierarchy.reference_refresh_reviewed",
                  details={"workflow_id": workflow_id, "map_revision": request.map_revision,
                           "map_sha256": request.expected_map_sha256,
                           "report_sha256": report["report_sha256"],
                           "selected_count": report["counts"]["selected_occurrences"],
                           "changed_count": report["counts"]["changed_occurrences"],
                           "blocker_count": report["counts"]["blockers"],
                           "rechecked": request.expected_report_sha256 is not None})
    return report
