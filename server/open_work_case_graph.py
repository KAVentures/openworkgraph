from __future__ import annotations

"""Read-only projection of the existing owner-reviewed Open Work declaration DAG.

This does not discover cases, infer obligations, verify source/customer outcomes,
or authorize agents. The caller must first obtain a consent/retention-filtered
snapshot from open_work_case_declarations.list_case_declarations.
"""
import hmac
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError

_MAX_CASES = 50
_MAX_DEPS = 8
_PROVENANCE = "explicit_owner_declaration_not_source_verified"


def project_declared_case_graph(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded, deterministic graph from one already-authorized snapshot.

    Missing prerequisites, duplicate case identities, cycles and contradictory
    claims fail closed rather than laundering a dependent into a root.
    """
    if not isinstance(snapshot, dict) or snapshot.get("format") != "openworkgraph.local-case-declarations.v1":
        raise CaseDeclarationError("invalid case declaration snapshot")
    items = snapshot.get("items")
    if not isinstance(items, list) or len(items) > _MAX_CASES:
        raise CaseDeclarationError("invalid bounded case declaration snapshot")

    by_ref: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise CaseDeclarationError("invalid declared case")
        ref = item.get("work_item_ref")
        deps = item.get("depends_on_work_item_refs")
        if (not isinstance(ref, str) or not ref.startswith("owg:w:") or
            len(ref) != 38 or any(ch not in "0123456789abcdef" for ch in ref[6:]) or
            ref in by_ref or not isinstance(deps, list) or len(deps) > _MAX_DEPS or
            len(set(map(str, deps))) != len(deps) or ref in deps or
            any(not isinstance(dep, str) or not dep.startswith("owg:w:") or
                len(dep) != 38 or any(ch not in "0123456789abcdef" for ch in dep[6:])
                for dep in deps) or
            item.get("provenance") != _PROVENANCE or
            item.get("expected_outcome_verified") is not False or
            item.get("business_obligation_verified") is not False or
            item.get("customer_outcome_verified") is not False or
            item.get("agent_execution_authorized") is not False or
            item.get("source_state") != "unknown_until_explicit_recheck"):
            raise CaseDeclarationError("invalid or overclaimed declared case")
        by_ref[ref] = item
        dependencies[ref] = deps

    if any(dep not in by_ref for deps in dependencies.values() for dep in deps):
        raise CaseDeclarationError("missing declared prerequisite")

    # A fixed-point traversal produces deterministic prerequisite-first levels.
    # It cannot reinterpret a corrupt cycle as an independently approved root.
    depths: dict[str, int] = {}
    pending = set(by_ref)
    while pending:
        ready = sorted(ref for ref in pending
                       if all(dep in depths for dep in dependencies[ref]))
        if not ready:
            raise CaseDeclarationError("cyclic declared dependency graph")
        for ref in ready:
            depths[ref] = 1 + max((depths[d] for d in dependencies[ref]), default=-1)
            pending.remove(ref)

    nodes = [
        {
            "work_item_ref": ref,
            "goal_code": by_ref[ref]["goal_code"],
            "expected_outcome_code": by_ref[ref]["expected_outcome_code"],
            "revision": by_ref[ref]["revision"],
            "updated_at": by_ref[ref]["updated_at"],
            "expires_at": by_ref[ref]["expires_at"],
            "dependency_depth": depths[ref],
            "declared_prerequisite_count": len(dependencies[ref]),
            "provenance": _PROVENANCE,
            "source_state": "unknown_until_explicit_recheck",
            "expected_outcome_verified": False,
            "business_obligation_verified": False,
            "customer_outcome_verified": False,
            "agent_execution_authorized": False,
        }
        for ref in sorted(by_ref, key=lambda ref: (depths[ref], ref))
    ]
    edges = [
        {
            "prerequisite_ref": dep,
            "dependent_ref": ref,
            "edge_type": "owner_declared_dependency",
            "provenance": _PROVENANCE,
            "source_verified": False,
            "business_obligation_verified": False,
        }
        for ref in sorted(dependencies)
        for dep in sorted(dependencies[ref])
    ]
    return {
        "format": "openworkgraph.local-case-dependency-graph.v1",
        "nodes": nodes,
        "edges": edges,
        "case_count": len(nodes),
        "declared_dependency_count": len(edges),
        "eligible_declaration_graph_complete": True,
        "source_inventory_complete": False,
        "source_read_performed": False,
        "source_state": "unknown_until_explicit_recheck",
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }




_EDIT_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def project_declared_case_change_impact(
    snapshot: dict[str, Any], *, work_item_ref: str,
    expected_revision: int, expected_edit_token: str,
    operation: str,
) -> dict[str, Any]:
    """Preview the owner approvals invalidated by a separate CAS edit/delete.

    A preview is a non-mutating, point-in-time snapshot. The later write must
    separately confirm intent and check the same revision and incarnation.
    """
    if operation not in ("revise", "delete"):
        raise CaseDeclarationError("invalid change operation")
    if (not isinstance(work_item_ref, str) or
        not re.fullmatch(r"owg:w:[0-9a-f]{32}", work_item_ref) or
        type(expected_revision) is not int or expected_revision < 1 or
        not isinstance(expected_edit_token, str) or
        not _EDIT_TOKEN.fullmatch(expected_edit_token)):
        raise CaseDeclarationError("invalid change preview request")

    graph = project_declared_case_graph(snapshot)
    matches = [item for item in snapshot["items"]
               if item["work_item_ref"] == work_item_ref]
    if len(matches) != 1:
        raise CaseDeclarationError("stale declaration: case not currently reviewed")
    item = matches[0]
    token = item.get("edit_token")
    if (type(item.get("revision")) is not int or
        item["revision"] != expected_revision or
        not isinstance(token, str) or not _EDIT_TOKEN.fullmatch(token) or
        not hmac.compare_digest(token, expected_edit_token)):
        raise CaseDeclarationError("stale declaration version or incarnation")

    children: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        children.setdefault(edge["prerequisite_ref"], []).append(edge["dependent_ref"])
    affected = {work_item_ref}
    frontier = [work_item_ref]
    while frontier:
        current = frontier.pop()
        for dependent in children.get(current, []):
            if dependent not in affected:
                affected.add(dependent)
                frontier.append(dependent)
    depth = {node["work_item_ref"]: node["dependency_depth"] for node in graph["nodes"]}
    dependents = sorted(affected - {work_item_ref},
                        key=lambda ref: (depth[ref], ref))
    return {
        "format": "openworkgraph.local-case-change-impact.v1",
        "work_item_ref": work_item_ref,
        "operation": operation,
        "expected_revision": expected_revision,
        "invalidated_dependent_refs": dependents,
        "invalidated_dependent_count": len(dependents),
        "affected_case_refs": [work_item_ref, *dependents],
        "affected_case_count": len(affected),
        "unaffected_case_count": graph["case_count"] - len(affected),
        "provenance": _PROVENANCE,
        "advisory_snapshot_only": True,
        "requires_separate_confirmed_cas_write": True,
        "mutation_performed": False,
        "source_inventory_complete": False,
        "source_read_performed": False,
        "source_state": "unknown_until_explicit_recheck",
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }

__all__ = ["project_declared_case_graph", "project_declared_case_change_impact"]
