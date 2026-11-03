"""Evidence-anchored interruption/resumption graph over local reconstructed episodes.

A resumption edge is a candidate continuity of observed activity, not a
business-case identity, task completion, case merge or permission.
"""
from __future__ import annotations

from typing import Any
from .reconstruction_review_overlay import _snapshot, provisional_activity_episodes

RESUMPTION_FORMAT = "openworkgraph.reconstruction-resumptions.v1"
MAX_EPISODES = 2048
MAX_INTERRUPTING_EPISODES = 16
MAX_INTERRUPTING_EVENTS = 128
MAX_RESUMPTIONS = 512


def _resumption_chains(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group only consecutive accepted returns to the same anchored work item.

    An abstained or missing edge breaks continuity: never bridge over an
    unverified return, even if the first and last item labels are equal.
    """
    active: dict[str, list[int]] = {}
    completed: list[list[int]] = []
    for index, edge in enumerate(edges):
        item = edge["linked_item_id"]
        current = active.get(item)
        if current and edges[current[-1]]["to_episode_id"] == edge["from_episode_id"]:
            current.append(index)
        else:
            if current and len(current) >= 2:
                completed.append(current)
            active[item] = [index]
    completed.extend(run for run in active.values() if len(run) >= 2)
    completed.sort(key=lambda run: run[0])

    chains: list[dict[str, Any]] = []
    for run in completed:
        members = [edges[index] for index in run]
        classes = [
            kind for kind in (
                "other_production_linked_item",
                "unassigned_observations",
                "experimental_proposal_gap",
            )
            if any(kind in member["interruption_classes"] for member in members)
        ]
        chains.append({
            "chain_id": f"chain_{len(chains) + 1:05d}",
            "linked_item_id": members[0]["linked_item_id"],
            "episode_ids": [members[0]["from_episode_id"]]
                           + [member["to_episode_id"] for member in members],
            "resumption_edge_indexes": run,
            "resumption_count": len(run),
            "intervening_observation_count": sum(
                member["intervening_observation_count"] for member in members
            ),
            "other_case_observation_count": sum(
                member["other_case_observation_count"] for member in members
            ),
            "unknown_observation_count": sum(
                member["unknown_observation_count"] for member in members
            ),
            "experimental_proposal_observation_count": sum(
                member["experimental_proposal_observation_count"]
                for member in members
            ),
            "interruption_classes": classes,
            "all_links_independently_anchored": True,
            "business_case_identity_verified": False,
            "canonical_membership_unchanged": True,
        })
    return chains


def reconstruct_resumptions(
    events: list[dict[str, Any]], packet: dict[str, Any],
) -> dict[str, Any]:
    """Link separated production-anchored episodes without merging events.

    Both endpoints require matching untainted literal source-record anchors,
    the same production-linked item and a complete capture scope. Proposals
    never become anchors. Intervening observations are negative context only.
    """
    timeline = provisional_activity_episodes(events, packet)
    expected, linked, _ = _snapshot(events)
    if type(packet) is not dict or packet != expected:
        raise ValueError("review packet is stale or altered; prepare again")
    episodes = timeline["episodes"]
    if len(episodes) > MAX_EPISODES:
        raise ValueError("too many episodes; select a smaller authorized window")
    rows = {row["event_id"]: row for row in linked["rows"]}
    last_anchored: dict[str, int] = {}
    edges: list[dict[str, Any]] = []
    abstentions: dict[str, int] = {}
    unanchored = 0

    def abstain(reason: str) -> None:
        abstentions[reason] = abstentions.get(reason, 0) + 1

    def anchor(episode: dict[str, Any]) -> tuple[tuple[str, str, str, str], str, str] | None:
        ids = episode["production_linked_event_ids"]
        if not ids:
            return None
        trusted = [rows[event_id] for event_id in ids]
        scope = trusted[0].get("scope")
        source = trusted[0].get("source")
        refs = [row.get("ref") for row in trusted if row.get("ref")]
        if (
            not isinstance(scope, tuple) or len(scope) != 4
            or not all(isinstance(part, str) and part for part in scope)
            or not isinstance(source, str) or source in ("", "unknown")
            or not refs or not isinstance(refs[0], str)
        ):
            return None
        if any(
            row.get("scope") != scope or row.get("source") != source
            or row.get("reference_tainted") or row.get("ephemeral_claim_tainted")
            or row.get("action_claim_malformed")
            for row in trusted
        ) or any(ref != refs[0] for ref in refs):
            return None
        return scope, source, refs[0]

    for end_index, episode in enumerate(episodes):
        item = episode["item_id"]
        if item is None or not episode["production_linked_event_ids"]:
            unanchored += 1
            continue
        current_anchor = anchor(episode)
        previous_index = last_anchored.get(item)
        last_anchored[item] = end_index
        if previous_index is None:
            continue
        start = episodes[previous_index]
        previous_anchor = anchor(start)
        if previous_anchor is None or current_anchor is None:
            abstain("missing_or_conflicting_literal_anchor")
            continue
        if previous_anchor[0] != current_anchor[0]:
            abstain("capture_scope_change")
            continue
        if previous_anchor[1:] != current_anchor[1:]:
            abstain("source_record_or_namespace_change")
            continue
        between = episodes[previous_index + 1:end_index]
        if not between:
            abstain("no_observed_interruption")
            continue
        if len(between) > MAX_INTERRUPTING_EPISODES:
            abstain("interruption_episode_budget_exceeded")
            continue
        intervening_ids = [eid for part in between for eid in part["event_ids"]]
        if len(intervening_ids) > MAX_INTERRUPTING_EVENTS:
            abstain("interruption_observation_budget_exceeded")
            continue
        span = [eid for part in episodes[previous_index:end_index + 1]
                for eid in part["event_ids"]]
        scope = current_anchor[0]
        if any(
            rows[eid].get("scope") != scope
            or rows[eid].get("reference_tainted")
            or rows[eid].get("ephemeral_claim_tainted")
            or rows[eid].get("action_claim_malformed")
            for eid in span
        ):
            abstain("untrusted_or_cross_scope_interruption")
            continue
        if any(rows[right]["observed_at"] <= rows[left]["observed_at"]
               for left, right in zip(span, span[1:])):
            abstain("nonmonotonic_or_tied_observations")
            continue
        other_case = sum(len(part["production_linked_event_ids"])
                         for part in between if part["item_id"] != item)
        unknown = sum(len(part["unassigned_event_ids"]) for part in between)
        provisional = sum(len(part["experimental_proposed_event_ids"])
                          for part in between)
        classes = []
        if other_case:
            classes.append("other_production_linked_item")
        if unknown:
            classes.append("unassigned_observations")
        if provisional:
            classes.append("experimental_proposal_gap")
        if not classes:
            abstain("no_classifiable_interruption")
            continue
        surfaces = {(rows[eid].get("app"), rows[eid].get("source"))
                    for eid in span}
        edges.append({
            "from_episode_id": start["episode_id"],
            "to_episode_id": episode["episode_id"],
            "linked_item_id": item,
            "intervening_episode_ids": [part["episode_id"] for part in between],
            "intervening_observation_count": len(intervening_ids),
            "other_case_observation_count": other_case,
            "unknown_observation_count": unknown,
            "experimental_proposal_observation_count": provisional,
            "interruption_classes": classes,
            "surface_changed_within_span": len(surfaces) > 1,
            "basis": "same_production_item_and_literal_source_record_at_both_endpoints",
            "observed_resumption_not_proven": True,
            "business_case_identity_verified": False,
            "canonical_membership_unchanged": True,
        })
        if len(edges) > MAX_RESUMPTIONS:
            raise ValueError("too many resumption links; select a smaller authorized window")

    chains = _resumption_chains(edges)
    chained_edge_count = sum(chain["resumption_count"] for chain in chains)
    return {
        "format": RESUMPTION_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "episode_count": len(episodes),
        "resumption_count": len(edges),
        "resumption_chain_count": len(chains),
        "resumption_chains": chains,
        "chained_resumption_edge_count": chained_edge_count,
        "standalone_resumption_edge_count": len(edges) - chained_edge_count,
        "unanchored_episode_count": unanchored,
        "abstention_counts": dict(sorted(abstentions.items())),
        "resumption_edges": edges,
        "episodes": episodes,
        "admitted_observation_count": timeline["admitted_observation_count"],
        "unadmitted_input_row_count": timeline["unadmitted_input_row_count"],
        "production_linked_event_count": timeline["production_linked_event_count"],
        "experimental_proposed_event_count": timeline["experimental_proposed_event_count"],
        "unassigned_event_count": timeline["unassigned_event_count"],
        "eligible_review_truncated": timeline["eligible_review_truncated"],
        "capture_completeness_verified": False,
        "source_content_included": False,
        "case_identity_verified": False,
        "production_linker_changed": False,
        "canonical_membership_unchanged": True,
        "authorizes_business_action": False,
    }
