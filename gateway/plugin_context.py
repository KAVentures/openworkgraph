"""Evidence-first orientation for the optional hosted MCP.

Every hint is derived from the SAME actor-scoped, retention-filtered canonical
rows returned by the Gateway. No inference may filter or replace those rows.
This module is pure, so deterministic tests can exercise missing/incorrect
resource pointers without starting OAuth or the collector.
"""
from __future__ import annotations

from typing import Any

from mcp_server.continuity import build_continuity_context


def make_orientation(
    recent: list[dict[str, Any]], *,
    returned_limit: int,
    scan_limit: int,
) -> dict[str, Any]:
    """Bounded recent evidence + conservative pointers + explicit raw fallback.

    `recent` is chronological rich privacy-hardened evidence, already filtered
    by authenticated actor and retention *before* entering this helper. Older
    evidence is intentionally not synthesized or declared absent.
    """
    visible = recent[-max(1, int(returned_limit)):]
    graph = build_continuity_context(recent, max_resources=12)
    graph["coverage"].update({
        "scope": "bounded_recent_gateway_tail",
        "recent_rows_scanned": len(recent),
        "scan_limit": scan_limit,
        "selected_rows_in_response": len(visible),
        "all_relevant_work_found": False,
        "candidate_recall_guaranteed": False,
        "source_evidence_remains_available_when_no_candidates": True,
    })
    observed_agent_events = any(
        row.get("source") == "agent" or str(row.get("event_type") or "").startswith("agent_")
        for row in recent
    )
    # An individual recent event is not enough to establish a complete agent run.
    # get_agent_runs separately reconstructs executions from bounded canonical
    # chronology. Do not invent run IDs or outcomes in this orientation graph.
    graph["agent_run_discovery"] = {
        "agent_events_observed_in_recent_tail": observed_agent_events,
        "tool": "get_agent_runs",
        "complete_run_inferred_from_tail": False,
    }
    return {
        "rows": visible,
        "returned": len(visible),
        "orientation": {
            "recent_canonical_evidence_available": bool(recent),
            "repeated_work_candidates_available": "not_checked",
            "nearby_agent_runs_available": observed_agent_events,
            "resource_candidates_available": bool(graph["resources"]),
            "hints_are_navigation_not_ground_truth": True,
        },
        "continuity_context": graph,
        "navigation_hints": (
            [
                {
                    "when": "reconstruct actual chronology, inspect surrounding actions, or verify a candidate",
                    "tool": "get_workflow_trace",
                    "reason": "raw privacy-hardened evidence is authoritative over derived grouping",
                },
                {
                    "when": "a specific keyword, resource, or work item is named",
                    "tool": "search_work",
                    "then": "get_workflow_trace without query across the relevant time window",
                    "reason": "lexical matches may omit nearby events, and zero matches do not prove absence",
                },
                {
                    "when": "repeated work or automation opportunities are requested",
                    "tool": "find_repeated_workflows",
                    "then": "get_workflow_evidence and get_workflow_trace",
                    "reason": "inferred clusters can be wrong or miss workflows; inspect canonical executions and other time windows",
                },
            ] if recent else [
                {
                    "when": "the user refers to older work or an inferred task is not found",
                    "tool": "get_workflow_trace",
                    "reason": "the recent tail is not a completeness check for the authorized history",
                },
            ]
        ),
        "raw_evidence_fallback": {
            "available": True,
            "tool": "get_workflow_trace",
            "strategy": "use explicit since/until, page using next_cursor until has_more is false; omit query to include unmatched surrounding events",
            "search_is_lexical": True,
            "zero_matches_prove_absence": False,
            "no_inferred_task_required": True,
            "canonical_metadata_preserved": True,
            "bounded_recent_overview_is_full_history": False,
            "gateway_contains_only_explicitly_synced_evidence": True,
        },
        "evidence_window": {
            "earliest_recent_observed_at": recent[0].get("observed_at") if recent else None,
            "latest_recent_observed_at": recent[-1].get("observed_at") if recent else None,
            "recent_rows_scanned": len(recent),
            "older_gateway_evidence_not_scanned": True,
        },
        "data_layer": "gateway_synced_privacy_hardened_evidence",
        "local_evidence_may_be_richer": True,
        "authoritative": False,
    }
