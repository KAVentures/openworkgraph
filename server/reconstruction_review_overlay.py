"""Local, evidence-bound human review overlay for uncertain OWG case assignments.

This module does not change canonical evidence or the automatic linker. A
confirmed assignment is a human annotation, never an observed/source edge.
Only existing visible singleton -> anchored-item weak candidates are eligible.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .work_item_links import link_observations, linked_work_items

PACKET_FORMAT = "openworkgraph.reconstruction-review-packet.v1"
DECISION_FORMAT = "openworkgraph.reconstruction-review-decisions.v1"
OVERLAY_FORMAT = "openworkgraph.reconstruction-reviewed-overlay.v1"
MAX_EVENTS = 10_000
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_DECISIONS = 24
ADJUDICATION_FORMAT = "openworkgraph.reconstruction-review-adjudication.v1"
RESOLUTION_FORMAT = "openworkgraph.reconstruction-review-resolution.v1"


def _source_digest(events: Any) -> str:
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("source must be a list of at most 10000 observations")
    try:
        canonical = json.dumps(
            events, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("source must contain finite JSON observations") from exc
    if len(canonical) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds the 8 MiB review budget")
    return hashlib.sha256(canonical).hexdigest()


_MAX_CONTEXT_INTERVENING = 8


def _observed_interleaving_context(
    linked: dict[str, Any], groups: list[list[str]], eligible: list[dict[str, Any]],
) -> dict[str, int]:
    """Annotate weak review candidates with bounded *negative* record context.

    Only contiguous same-capture-scope, same-app/source observations can
    establish a before/after record anchor. This never proposes a membership
    edge or treats adjacent observations as the same task. A nearer unknown
    record, timestamp tie, taint or capture boundary stops the search.
    """
    rows = linked["rows"]
    positions = {row["event_id"]: index for index, row in enumerate(rows)}
    item_of = {
        event_id: f"linked_item_{number:02d}"
        for number, members in enumerate(groups, start=1)
        for event_id in members
    }
    counts: dict[str, int] = {}
    for candidate in eligible:
        pivot = rows[positions[candidate["event_id"]]]
        scope = pivot.get("scope")
        valid = (
            isinstance(scope, tuple) and len(scope) == 4
            and all(isinstance(value, str) and value for value in scope)
            and isinstance(pivot.get("app"), str) and bool(pivot["app"])
            and isinstance(pivot.get("source"), str)
            and pivot["source"] not in ("", "unknown")
            and not pivot.get("reference_tainted")
            and not pivot.get("ephemeral_claim_tainted")
        )

        def anchor(direction: int) -> str | None:
            for distance in range(1, _MAX_CONTEXT_INTERVENING + 2):
                index = positions[pivot["event_id"]] + direction * distance
                if index < 0 or index >= len(rows):
                    break
                row = rows[index]
                if (
                    row.get("scope") != scope
                    or row.get("app") != pivot["app"]
                    or row.get("source") != pivot["source"]
                    or row.get("reference_tainted")
                    or row.get("ephemeral_claim_tainted")
                    or (direction < 0 and row["observed_at"] >= pivot["observed_at"])
                    or (direction > 0 and row["observed_at"] <= pivot["observed_at"])
                ):
                    break
                if row.get("ref"):
                    # A nearer unlinked record is a hard boundary, not a
                    # reason to search further for a convenient visible item.
                    return item_of.get(row["event_id"])
            return None

        before = anchor(-1) if valid else None
        after = anchor(1) if valid else None
        if not valid:
            status = "insufficient_capture_context"
        elif before and after:
            status = ("competing_record_anchors" if before != after
                      else "matching_linked_item_anchors")
        elif before or after:
            status = "one_sided_record_anchor"
        else:
            status = "no_bounded_record_anchors"
        candidate["interleaving_context"] = {
            "status": status,
            "preceding_linked_item_id": before,
            "following_linked_item_id": after,
            "maximum_intervening_observations_per_side": _MAX_CONTEXT_INTERVENING,
            "case_identity_verified": False,
            "auto_assigned": False,
        }
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _snapshot(events: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[list[str]]]:
    digest = _source_digest(events)
    projected = linked_work_items(events, max_items=50)
    if projected["total_items"] > len(projected["items"]):
        raise ValueError("too many linked items; select a smaller authorized window")
    linked = link_observations(events)
    rows = {row["event_id"]: row for row in linked["rows"]}
    groups = sorted(
        (ids for ids in linked["members"].values() if len(ids) >= 2),
        key=lambda ids: (-len(ids), rows[ids[0]]["observed_at"], ids[0]),
    )
    if len(groups) != len(projected["items"]):
        raise ValueError("inconsistent linker projection")
    # Both queues come from the production linker, not from caller-provided
    # candidate IDs. Two-hop paths are review hints, never automatic edges.
    if not projected["ambiguous_fragment_review_scan_complete"]:
        raise ValueError("two-hop evidence scan incomplete; select a smaller window")
    eligible = []
    for entry in projected["ambiguous_assignment_review_queue"]:
        eligible.append({
            "event_id": entry["event_id"],
            "evidence_hops": 1,
            "candidate_items": [
                {"item_id": target["item_id"], "weak_relations": target["weak_relations"]}
                for target in entry["candidate_items"]
            ],
            "candidate_items_truncated": entry["candidate_items_truncated"],
        })
    for entry in projected["ambiguous_fragment_review_queue"]:
        eligible.append({
            "event_id": entry["event_id"],
            "evidence_hops": 2,
            "candidate_items": [
                {
                    "item_id": target["item_id"],
                    "witness_event_path": target["witness_event_path"],
                    "weak_relation_path": target["weak_relation_path"],
                    "distinct_intermediary_count": target["distinct_intermediary_count"],
                }
                for target in entry["candidate_items"]
            ],
            "candidate_items_truncated": entry["candidate_items_truncated"],
        })
    if len({entry["event_id"] for entry in eligible}) != len(eligible):
        raise ValueError("overlapping direct and fragment review candidates")
    context_counts = _observed_interleaving_context(linked, groups, eligible[:MAX_DECISIONS])
    direct_total = projected["ambiguous_assignment_review_total"]
    fragment_total = projected["ambiguous_fragment_review_total"]
    packet = {
        "format": PACKET_FORMAT,
        "evidence_sha256": digest,
        "admitted_observation_count": len(linked["rows"]),
        "linked_item_count": len(groups),
        "eligible_review_total": direct_total + fragment_total,
        "eligible_direct_review_total": direct_total,
        "eligible_fragment_review_total": fragment_total,
        "eligible_review_truncated": (
            projected["ambiguous_assignment_review_truncated"]
            or projected["ambiguous_fragment_review_truncated"]
            or len(eligible) > MAX_DECISIONS
        ),
        "eligible_candidates": eligible[:MAX_DECISIONS],
        "observed_interleaving_context_counts": context_counts,
        "source_content_included": False,
        "canonical_membership_unchanged": True,
    }
    return packet, linked, groups



AUTO_PROPOSAL_FORMAT = "openworkgraph.reconstruction-auto-proposals.v1"


def propose_automatic_review(
    events: list[dict[str, Any]], packet: dict[str, Any],
) -> dict[str, Any]:
    """Experimental two-sided record proposals, including witnessed fragments.

    A two-hop fragment may be proposed only after its *actual* weak-path
    intermediary independently qualifies for a direct, exact-record bracket
    proposal to the same item. This is one bounded propagation step, never a
    transitive chain, source assertion or canonical membership change.
    """
    expected, linked, groups = _snapshot(events)
    if type(packet) is not dict or packet != expected:
        raise ValueError("review packet is stale or altered; prepare again")
    rows = linked["rows"]
    positions = {row["event_id"]: index for index, row in enumerate(rows)}
    item_of = {
        event_id: f"linked_item_{index:02d}"
        for index, members in enumerate(groups, start=1)
        for event_id in members
    }
    proposals: list[dict[str, str]] = []
    abstentions: dict[str, int] = {}
    # The literal source reference is used only in-memory for matching. Never
    # write it to the proposal artifact or expose it in the CLI output.
    direct_seed_records: dict[str, tuple[str, Any]] = {}

    def abstain(reason: str) -> None:
        abstentions[reason] = abstentions.get(reason, 0) + 1

    def exact_brackets(event_id: str) -> tuple[str, Any] | None:
        pivot = rows[positions[event_id]]
        scope = pivot.get("scope")
        if (
            not isinstance(scope, tuple) or len(scope) != 4
            or not all(isinstance(part, str) and part for part in scope)
            or not isinstance(pivot.get("app"), str) or not pivot["app"]
            or not isinstance(pivot.get("source"), str)
            or pivot["source"] in ("", "unknown")
            or pivot.get("reference_tainted") or pivot.get("ephemeral_claim_tainted")
            or pivot.get("action_claim_malformed") or pivot.get("ref")
        ):
            return None

        def anchor(direction: int) -> tuple[str | None, Any] | None:
            previous_time = pivot["observed_at"]
            for distance in range(1, _MAX_CONTEXT_INTERVENING + 2):
                index = positions[event_id] + direction * distance
                if not 0 <= index < len(rows):
                    return None
                row = rows[index]
                timestamp = row["observed_at"]
                if (
                    row.get("scope") != scope or row.get("app") != pivot["app"]
                    or row.get("source") != pivot["source"]
                    or row.get("reference_tainted")
                    or row.get("ephemeral_claim_tainted")
                    or row.get("action_claim_malformed")
                    or (direction < 0 and timestamp >= previous_time)
                    or (direction > 0 and timestamp <= previous_time)
                ):
                    return None
                previous_time = timestamp
                if row.get("ref"):
                    return item_of.get(row["event_id"]), row["ref"]
            return None

        before, after = anchor(-1), anchor(1)
        if (before is None or after is None or before[0] is None
                or before[0] != after[0] or before[1] != after[1]):
            return None
        return before[0], before[1]

    # Stage one: a direct weak edge plus two independent matching literal
    # anchors. Candidate list completeness is mandatory for each proposal.
    for candidate in expected["eligible_candidates"]:
        if candidate["evidence_hops"] != 1:
            continue
        targets = candidate["candidate_items"]
        if candidate["candidate_items_truncated"] or len(targets) != 1:
            abstain("competing_or_incomplete_candidates")
            continue
        bracket = exact_brackets(candidate["event_id"])
        target = targets[0]["item_id"]
        if bracket is None or bracket[0] != target:
            abstain("missing_or_conflicting_exact_record_brackets")
            continue
        direct_seed_records[candidate["event_id"]] = bracket
        proposals.append({
            "event_id": candidate["event_id"],
            "item_id": target,
            "basis": "same_literal_record_brackets_and_unique_weak_candidate",
        })

    # Stage two: no recursive propagation. A fragment must have a single
    # witnessed target and a directly proposed intermediary, with its own
    # independently verified brackets for the *same literal record*.
    for candidate in expected["eligible_candidates"]:
        if candidate["evidence_hops"] != 2:
            continue
        targets = candidate["candidate_items"]
        if candidate["candidate_items_truncated"] or len(targets) != 1:
            abstain("competing_or_incomplete_two_hop_candidates")
            continue
        target = targets[0]
        witness = target.get("witness_event_path")
        if (not isinstance(witness, list) or len(witness) != 3
                or witness[0] != candidate["event_id"]):
            abstain("invalid_two_hop_witness")
            continue
        middle_seed = direct_seed_records.get(witness[1])
        if middle_seed is None or middle_seed[0] != target["item_id"]:
            abstain("two_hop_missing_anchored_direct_seed")
            continue
        bracket = exact_brackets(candidate["event_id"])
        if bracket is None or bracket != middle_seed:
            abstain("two_hop_missing_or_conflicting_record_brackets")
            continue
        proposals.append({
            "event_id": candidate["event_id"],
            "item_id": target["item_id"],
            "basis": "same_literal_record_brackets_and_anchored_direct_two_hop_seed",
        })
    direct_count = len(direct_seed_records)
    return {
        "format": AUTO_PROPOSAL_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "proposed_assignments": proposals,
        "proposal_count": len(proposals),
        "direct_proposal_count": direct_count,
        "two_hop_proposal_count": len(proposals) - direct_count,
        "abstention_counts": dict(sorted(abstentions.items())),
        "visible_candidate_count": len(expected["eligible_candidates"]),
        "eligible_review_total": expected["eligible_review_total"],
        "eligible_review_truncated": expected["eligible_review_truncated"],
        "case_identity_verified": False,
        "automatic_proposals_not_canonical_membership": True,
        "canonical_membership_unchanged": True,
        "authorizes_business_action": False,
    }



EPISODE_FORMAT = "openworkgraph.reconstruction-provisional-episodes.v1"


def provisional_activity_episodes(
    events: list[dict[str, Any]], packet: dict[str, Any],
) -> dict[str, Any]:
    """Partition observed time into interruption-aware, noncanonical episodes.

    Production membership and experimental proposals remain separate evidence
    classes. A later return to the same linked item starts a new episode after
    another item, an unknown span, a capture boundary or a different record.
    Never merge episodes by elapsed time or infer a business-case identity.
    """
    expected, linked, groups = _snapshot(events)
    if type(packet) is not dict or packet != expected:
        raise ValueError("review packet is stale or altered; prepare again")
    proposal = propose_automatic_review(events, packet)
    production = {
        event_id: f"linked_item_{index:02d}"
        for index, members in enumerate(groups, start=1)
        for event_id in members
    }
    suggested = {
        entry["event_id"]: entry["item_id"]
        for entry in proposal["proposed_assignments"]
    }
    rows = linked["rows"]
    episodes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    current_record: str | None = None
    linked_count = proposed_count = unassigned_count = 0
    boundary_counts: dict[str, int] = {}

    for row in rows:
        event_id = row["event_id"]
        item_id = production.get(event_id) or suggested.get(event_id)
        basis = (
            "production_linked" if event_id in production
            else "experimental_proposal" if event_id in suggested
            else "unassigned"
        )
        if basis == "production_linked":
            linked_count += 1
        elif basis == "experimental_proposal":
            proposed_count += 1
        else:
            unassigned_count += 1

        boundary = None
        if previous is None:
            boundary = "window_start"
        elif row["observed_at"] <= previous["observed_at"]:
            boundary = "timestamp_tie"
        elif row.get("scope") != previous.get("scope"):
            boundary = "capture_scope_change"
        elif (row.get("app"), row.get("source")) != (
            previous.get("app"), previous.get("source")
        ):
            boundary = "surface_change"
        elif item_id != current["item_id"]:
            boundary = "item_switch_or_unknown_gap"
        elif row.get("ref") and current_record and row["ref"] != current_record:
            boundary = "different_record_anchor"

        if boundary is not None:
            current = {
                "episode_id": f"episode_{len(episodes) + 1:05d}",
                "item_id": item_id,
                "event_ids": [],
                "production_linked_event_ids": [],
                "experimental_proposed_event_ids": [],
                "unassigned_event_ids": [],
                "start_boundary": boundary,
                "source_record_identity_verified": False,
                "business_case_identity_verified": False,
            }
            episodes.append(current)
            boundary_counts[boundary] = boundary_counts.get(boundary, 0) + 1
            current_record = None

        current["event_ids"].append(event_id)
        current[{
            "production_linked": "production_linked_event_ids",
            "experimental_proposal": "experimental_proposed_event_ids",
            "unassigned": "unassigned_event_ids",
        }[basis]].append(event_id)
        if row.get("ref"):
            current_record = row["ref"]
        previous = row

    return {
        "format": EPISODE_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "input_observation_count": len(events),
        "admitted_observation_count": len(rows),
        "unadmitted_input_row_count": len(events) - len(rows),
        "episode_count": len(episodes),
        "production_linked_event_count": linked_count,
        "experimental_proposed_event_count": proposed_count,
        "unassigned_event_count": unassigned_count,
        "direct_proposal_count": proposal["direct_proposal_count"],
        "two_hop_proposal_count": proposal["two_hop_proposal_count"],
        "eligible_review_total": expected["eligible_review_total"],
        "eligible_review_truncated": expected["eligible_review_truncated"],
        "boundary_counts": dict(sorted(boundary_counts.items())),
        "episodes": episodes,
        "source_content_included": False,
        "case_identity_verified": False,
        "production_linker_changed": False,
        "canonical_membership_unchanged": True,
        "authorizes_business_action": False,
    }



def score_automatic_proposals(
    events: list[dict[str, Any]], packet: dict[str, Any], truth: dict[str, Any],
) -> dict[str, Any]:
    """Compare deterministic proposals with production baseline on independent labels.

    Reuse the existing complete-annotation scorer, not human decisions or a
    different metric definition. These results are a local *experimental*
    automatic overlay, not the shipped linker's F1 or frozen holdout.
    """
    proposed = propose_automatic_review(events, packet)
    decisions = {
        "format": DECISION_FORMAT,
        "evidence_sha256": proposed["evidence_sha256"],
        "decisions": [
            {"event_id": entry["event_id"], "item_id": entry["item_id"],
             "action": "confirm"}
            for entry in proposed["proposed_assignments"]
        ],
    }
    measured = score_review(events, packet, decisions, truth)
    return {
        "format": "openworkgraph.reconstruction-auto-proposal-score.v1",
        "annotated_event_count": measured["annotated_event_count"],
        "visible_candidate_count": proposed["visible_candidate_count"],
        "proposal_count": proposed["proposal_count"],
        "direct_proposal_count": proposed["direct_proposal_count"],
        "two_hop_proposal_count": proposed["two_hop_proposal_count"],
        "abstention_counts": proposed["abstention_counts"],
        "eligible_review_truncated": proposed["eligible_review_truncated"],
        "automatic_baseline": measured["automatic_baseline"],
        "experimental_automatic_proposal_overlay": measured["human_assisted_overlay"],
        "canonical_membership_unchanged": True,
        "preregistered_family_holdout": False,
        "production_linker_changed": False,
        "real_pilot_validated": False,
        "authorizes_business_action": False,
    }


def prepare_review_packet(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce only opaque IDs and typed candidate bases; never raw work text."""
    return _snapshot(events)[0]


def adjudicate_review(
    events: list[dict[str, Any]], packet: dict[str, Any],
    reviewer_a: dict[str, Any], reviewer_b: dict[str, Any],
) -> dict[str, Any]:
    """Require two explicit, identical decisions before any reviewed assignment.

    Both input reviews are independently validated against the same fresh
    production-linker snapshot. Reviewer independence is NOT authenticated;
    no weak edge is promoted to canonical or source-verified membership.
    """
    expected, _linked, _groups = _snapshot(events)
    if type(packet) is not dict or packet != expected:
        raise ValueError("review packet is stale or altered; prepare again")
    for reviewer in (reviewer_a, reviewer_b):
        if type(reviewer) is not dict or reviewer.get("format") != DECISION_FORMAT:
            raise ValueError("adjudication requires two original decision documents")
        # Includes per-reviewer two-hop contradiction, scope and candidate checks.
        apply_review(events, packet, reviewer)
    left = {row["event_id"]: row for row in reviewer_a["decisions"]}
    right = {row["event_id"]: row for row in reviewer_b["decisions"]}
    accepted = []
    unresolved = []
    untouched = 0
    for candidate in expected["eligible_candidates"]:
        event_id = candidate["event_id"]
        a, b = left.get(event_id), right.get(event_id)
        if a is None and b is None:
            untouched += 1
        elif a is not None and b is not None and a == b:
            accepted.append(dict(a))
        else:
            unresolved.append(event_id)
    consensus = {
        "format": DECISION_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "decisions": accepted,
    }
    # A consensus assembled from individually valid decisions must also be
    # internally coherent when two-hop and intermediary decisions interact.
    apply_review(events, packet, consensus)
    return {
        "format": ADJUDICATION_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "reviewer_a": reviewer_a,
        "reviewer_b": reviewer_b,
        "consensus_decisions": consensus,
        "consensus_confirmed_count": sum(row["action"] == "confirm" for row in accepted),
        "consensus_rejected_count": sum(row["action"] == "reject" for row in accepted),
        "unresolved_event_ids": unresolved,
        "unreviewed_visible_count": untouched,
        "eligible_review_total": expected["eligible_review_total"],
        "eligible_review_truncated": expected["eligible_review_truncated"],
        "reviewer_independence_verified": False,
        "human_consensus_not_source_evidence": True,
        "canonical_membership_unchanged": True,
    }



def resolve_review_disagreement(
    events: list[dict[str, Any]], packet: dict[str, Any],
    adjudication: dict[str, Any], arbiter: dict[str, Any],
) -> dict[str, Any]:
    """Resolve only contested review rows with an explicit third human decision.

    A third review is NOT an automatic tie-break or proof of reviewer identity.
    All three inputs and the combined decisions are replayed against the current
    production-linker snapshot. Prior consensus cannot be changed.
    """
    if type(adjudication) is not dict or adjudication.get("format") != ADJUDICATION_FORMAT:
        raise ValueError("resolution requires a two-reviewer adjudication artifact")
    expected = adjudicate_review(
        events, packet, adjudication.get("reviewer_a"), adjudication.get("reviewer_b"),
    )
    if adjudication != expected:
        raise ValueError("adjudication artifact is stale or altered")
    if type(arbiter) is not dict or arbiter.get("format") != DECISION_FORMAT:
        raise ValueError("resolution requires an original third-reviewer decision document")
    # Independently validate all third-reviewer choices and intermediary paths.
    apply_review(events, packet, arbiter)
    unresolved = set(expected["unresolved_event_ids"])
    new_rows = arbiter["decisions"]
    if not new_rows or any(row["event_id"] not in unresolved for row in new_rows):
        raise ValueError("third reviewer may decide only unresolved review rows")
    previous = expected["consensus_decisions"]["decisions"]
    combined_by_id = {row["event_id"]: row for row in previous + new_rows}
    # Preserve canonical packet ordering rather than trusting input order.
    combined = {
        "format": DECISION_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "decisions": [
            dict(combined_by_id[row["event_id"]])
            for row in packet["eligible_candidates"]
            if row["event_id"] in combined_by_id
        ],
    }
    # A third review must not contradict an agreed intermediary or two-hop
    # decision, even when both documents are individually valid.
    apply_review(events, packet, combined)
    remaining = [
        event_id for event_id in expected["unresolved_event_ids"]
        if event_id not in {row["event_id"] for row in new_rows}
    ]
    return {
        "format": RESOLUTION_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "adjudication": adjudication,
        "third_reviewer_decisions": arbiter,
        "resolved_decisions": combined,
        "third_reviewer_resolved_count": len(new_rows),
        "remaining_unresolved_event_ids": remaining,
        "reviewer_independence_verified": False,
        "human_resolution_not_source_evidence": True,
        "canonical_membership_unchanged": True,
    }


def apply_review(
    events: list[dict[str, Any]], packet: dict[str, Any],
    decisions: dict[str, Any],
) -> dict[str, Any]:
    """Apply explicit analyst annotations to a *separate* read-only projection.

    Fail closed on any source change, forged packet, hidden target, unsupported
    edge, duplicate decision or malformed input. A rejected candidate remains
    unassigned; approval never authorizes an agent or a provider write.
    """
    expected, linked, groups = _snapshot(events)
    if type(packet) is not dict or packet != expected:
        raise ValueError("review packet is stale or altered; prepare again")
    adjudication = None
    resolution = None
    if type(decisions) is dict and decisions.get("format") == RESOLUTION_FORMAT:
        if set(decisions) != {
            "format", "evidence_sha256", "adjudication", "third_reviewer_decisions",
            "resolved_decisions", "third_reviewer_resolved_count",
            "remaining_unresolved_event_ids", "reviewer_independence_verified",
            "human_resolution_not_source_evidence", "canonical_membership_unchanged",
        }:
            raise ValueError("invalid resolution artifact fields")
        resolution = resolve_review_disagreement(
            events, packet, decisions["adjudication"], decisions["third_reviewer_decisions"],
        )
        if decisions != resolution:
            raise ValueError("resolution artifact was altered; resolve again")
        adjudication = resolution["adjudication"]
        decisions = resolution["resolved_decisions"]
    if type(decisions) is dict and decisions.get("format") == ADJUDICATION_FORMAT:
        if set(decisions) != {
            "format", "evidence_sha256", "reviewer_a", "reviewer_b",
            "consensus_decisions", "consensus_confirmed_count",
            "consensus_rejected_count", "unresolved_event_ids",
            "unreviewed_visible_count", "eligible_review_total",
            "eligible_review_truncated", "reviewer_independence_verified",
            "human_consensus_not_source_evidence", "canonical_membership_unchanged",
        }:
            raise ValueError("invalid adjudication artifact fields")
        adjudication = adjudicate_review(
            events, packet, decisions["reviewer_a"], decisions["reviewer_b"],
        )
        if decisions != adjudication:
            raise ValueError("adjudication artifact was altered; re-adjudicate")
        decisions = adjudication["consensus_decisions"]
    if type(decisions) is not dict or set(decisions) != {
        "format", "evidence_sha256", "decisions",
    }:
        raise ValueError("invalid decisions document")
    if (decisions["format"] != DECISION_FORMAT
            or decisions["evidence_sha256"] != expected["evidence_sha256"]):
        raise ValueError("decisions are not bound to this exact evidence snapshot")
    rows = decisions["decisions"]
    if type(rows) is not list or len(rows) > MAX_DECISIONS:
        raise ValueError("too many or malformed review decisions")
    eligible = {
        entry["event_id"]: {
            target["item_id"]: entry["evidence_hops"]
            for target in entry["candidate_items"]
        }
        for entry in expected["eligible_candidates"]
    }
    singleton_ids = {
        ids[0] for ids in linked["members"].values() if len(ids) == 1
    }
    reviewed: dict[str, list[str]] = {
        f"linked_item_{index:02d}": list(ids)
        for index, ids in enumerate(groups, start=1)
    }
    confirmations: dict[str, str] = {}
    confirmation_hops: dict[str, int] = {}
    rejected: list[dict[str, str]] = []
    review_decisions: dict[str, tuple[str, str]] = {}
    seen: set[str] = set()
    for decision in rows:
        if type(decision) is not dict or set(decision) != {
            "event_id", "item_id", "action",
        }:
            raise ValueError("invalid review decision fields")
        event_id, item_id, action = (
            decision["event_id"], decision["item_id"], decision["action"]
        )
        if (type(event_id) is not str or type(item_id) is not str
                or event_id in seen or event_id not in singleton_ids
                or item_id not in eligible.get(event_id, set())
                or action not in ("confirm", "reject")):
            raise ValueError("duplicate, hidden or unsupported review decision")
        seen.add(event_id)
        review_decisions[event_id] = (item_id, action)
        if action == "confirm":
            confirmations[event_id] = item_id
            confirmation_hops[event_id] = eligible[event_id][item_id]
            # A two-hop confirmation attaches only the reviewed loose event.
            # Its unassigned intermediary is never transitively promoted.
            reviewed[item_id].append(event_id)
        else:
            rejected.append({"event_id": event_id, "item_id": item_id})
    # A two-hop review cannot use an intermediary that the same reviewer
    # explicitly rejected for this item or confirmed for a competing item.
    # Decisions are checked together, independent of their input ordering.
    for entry in expected["eligible_candidates"]:
        loose_id = entry["event_id"]
        if entry["evidence_hops"] != 2 or loose_id not in confirmations:
            continue
        target_id = confirmations[loose_id]
        witness = next(
            target for target in entry["candidate_items"]
            if target["item_id"] == target_id
        )
        middle_id = witness["witness_event_path"][1]
        middle_decision = review_decisions.get(middle_id)
        if middle_decision is not None and (
            (middle_decision[1] == "reject" and middle_decision[0] == target_id)
            or (middle_decision[1] == "confirm" and middle_decision[0] != target_id)
        ):
            raise ValueError("conflicting human decisions along two-hop evidence path")
    position = {row["event_id"]: index for index, row in enumerate(linked["rows"])}
    items = []
    for item_id, members in reviewed.items():
        members.sort(key=position.__getitem__)
        confirmed = sorted(
            (event_id for event_id, target in confirmations.items()
             if target == item_id), key=position.__getitem__,
        )
        items.append({
            "item_id": item_id,
            "event_ids": members,
            "human_confirmed_event_ids": confirmed,
            "human_confirmed_direct_event_ids": [
                event_id for event_id in confirmed if confirmation_hops[event_id] == 1
            ],
            "human_confirmed_two_hop_event_ids": [
                event_id for event_id in confirmed if confirmation_hops[event_id] == 2
            ],
            "automatic_member_count": len(members) - len(confirmed),
            "membership_status": "human_reviewed_overlay" if confirmed else "automatic_evidence",
        })
    return {
        "format": OVERLAY_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "items": items,
        "human_confirmed_count": len(confirmations),
        "human_confirmed_direct_count": sum(hops == 1 for hops in confirmation_hops.values()),
        "human_confirmed_two_hop_count": sum(hops == 2 for hops in confirmation_hops.values()),
        "human_rejected_count": len(rejected),
        "human_rejections": rejected,
        "eligible_unreviewed_count": expected["eligible_review_total"] - len(seen),
        "eligible_review_truncated": expected["eligible_review_truncated"],
        "unassigned_event_ids": sorted(singleton_ids - set(confirmations), key=position.__getitem__),
        "canonical_membership_unchanged": True,
        "human_decisions_are_not_source_evidence": True,
        "review_decision_provenance": (
            "third_reviewer_resolution" if resolution else
            "two_reviewer_consensus" if adjudication else "single_reviewer"
        ),
        "unresolved_reviewer_disagreement_count": (
            len(resolution["remaining_unresolved_event_ids"]) if resolution else
            len(adjudication["unresolved_event_ids"]) if adjudication else 0
        ),
        "reviewer_independence_verified": False,
        "authorizes_business_action": False,
    }


def score_review(
    events: list[dict[str, Any]], packet: dict[str, Any],
    decisions: dict[str, Any], truth: dict[str, Any],
) -> dict[str, Any]:
    """Score complete independent annotations, separating automatic vs assisted.

    This is *not* the preregistered blind family holdout or a measure of
    automatic reconstruction quality after analyst confirmation.
    """
    from evals.reconstruction.scoring import score_case

    overlay = apply_review(events, packet, decisions)
    _packet, linked, groups = _snapshot(events)
    admitted = {row["event_id"] for row in linked["rows"]}
    # The shared scorer uses pairwise ordering and a truth-workflow subset DP.
    # Do not let an oversized local annotation turn into unbounded CPU work.
    if len(admitted) > 500:
        raise ValueError("score a window of at most 500 admitted observations")
    if type(truth) is not dict or set(truth) != {"workflows", "noise_event_ids"}:
        raise ValueError("truth must contain workflows and noise_event_ids")
    workflows = truth["workflows"]
    noise = truth["noise_event_ids"]
    if type(workflows) is not list or type(noise) is not list or len(workflows) > 12:
        raise ValueError("malformed or excessive independent annotations")
    labeled: list[str] = []
    for item in workflows:
        if type(item) is not dict or set(item) != {"event_ids"} or type(item["event_ids"]) is not list:
            raise ValueError("malformed annotated workflow")
        if not item["event_ids"]:
            raise ValueError("empty annotated workflow")
        labeled.extend(item["event_ids"])
    labeled.extend(noise)
    if (any(type(event_id) is not str for event_id in labeled)
            or len(labeled) != len(set(labeled))
            or set(labeled) != admitted):
        raise ValueError("annotations must partition every admitted event exactly once")
    case = {
        "case_id": "local_reviewed_session",
        "presented_evidence": [{"event_id": event_id} for event_id in sorted(admitted)],
        "ground_truth": truth,
    }
    def scored(member_groups: list[list[str]]) -> dict[str, Any]:
        assigned = {event_id for group in member_groups for event_id in group}
        prediction = {
            "workflows": [
                {"event_ids": group, "ordered_event_ids": group}
                for group in member_groups
            ],
            "unassigned_event_ids": sorted(admitted - assigned),
        }
        result = score_case(case, prediction)
        return {key: result[key] for key in (
            "workflow_assignment_precision", "workflow_assignment_recall",
            "workflow_assignment_f1", "cross_workflow_contamination",
            "interruption_rejection",
        )}
    return {
        "format": "openworkgraph.reconstruction-review-score.v1",
        "evidence_sha256": packet["evidence_sha256"],
        "annotated_event_count": len(admitted),
        "human_confirmed_count": overlay["human_confirmed_count"],
        "human_confirmed_direct_count": overlay["human_confirmed_direct_count"],
        "human_confirmed_two_hop_count": overlay["human_confirmed_two_hop_count"],
        "review_decision_provenance": overlay["review_decision_provenance"],
        "unresolved_reviewer_disagreement_count": overlay["unresolved_reviewer_disagreement_count"],
        "automatic_baseline": scored(groups),
        "human_assisted_overlay": scored([item["event_ids"] for item in overlay["items"]]),
        "complete_local_annotations_required": True,
        "preregistered_family_holdout": False,
        "automatic_model_improvement_claim": False,
        "real_pilot_validated": False,
    }


# Multi-session audits are descriptive local measurements, not preregistered
# family-holdout certification. Sessions are the statistical unit.
BATCH_FORMAT = "openworkgraph.reconstruction-review-batch.v1"
MAX_BATCH_SESSIONS = 24
_AUDIT_METRICS = (
    "workflow_assignment_precision", "workflow_assignment_recall",
    "workflow_assignment_f1", "cross_workflow_contamination",
    "interruption_rejection",
)


def aggregate_review_scores(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate independent local score_review outputs without exposing IDs.

    Each complete session receives equal weight; no per-event or source fields
    are returned. A duplicated source snapshot is refused rather than counted
    twice. The numeric threshold check is *local only*, never a holdout claim.
    """
    import math

    if type(scores) is not list or not 2 <= len(scores) <= MAX_BATCH_SESSIONS:
        raise ValueError("batch audit requires 2 to 24 independently annotated sessions")
    seen_digests: set[str] = set()
    sessions = []
    for index, report in enumerate(scores, start=1):
        if type(report) is not dict or report.get("format") != (
            "openworkgraph.reconstruction-review-score.v1"
        ):
            raise ValueError("batch requires complete local reconstruction score reports")
        digest = report.get("evidence_sha256")
        if (type(digest) is not str or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or digest in seen_digests):
            raise ValueError("invalid or duplicate evidence snapshot in batch")
        seen_digests.add(digest)
        count = report.get("annotated_event_count")
        if type(count) is not int or not 1 <= count <= 500:
            raise ValueError("invalid annotated observation count")
        metrics: dict[str, dict[str, float]] = {}
        for key in ("automatic_baseline", "human_assisted_overlay"):
            raw = report.get(key)
            if type(raw) is not dict:
                raise ValueError("missing reconstruction score metrics")
            metrics[key] = {}
            for name in _AUDIT_METRICS:
                value = raw.get(name)
                if (type(value) not in (int, float) or not math.isfinite(value)
                        or not 0 <= value <= 1):
                    raise ValueError("nonfinite or out-of-range reconstruction metric")
                metrics[key][name] = float(value)
        confirmed = report.get("human_confirmed_count")
        if type(confirmed) is not int or not 0 <= confirmed <= count:
            raise ValueError("invalid human confirmation count")
        provenance = report.get("review_decision_provenance", "single_reviewer")
        if provenance not in ("single_reviewer", "two_reviewer_consensus",
                            "third_reviewer_resolution"):
            raise ValueError("invalid review provenance")
        unresolved = report.get("unresolved_reviewer_disagreement_count", 0)
        if type(unresolved) is not int or not 0 <= unresolved <= count:
            raise ValueError("invalid unresolved reviewer disagreement count")
        sessions.append({
            "review_decision_provenance": provenance,
            "unresolved_reviewer_disagreement_count": unresolved,
            "session_index": index,
            "annotated_observation_count": count,
            "human_confirmed_count": confirmed,
            **metrics,
        })
    def macro(key: str) -> dict[str, float]:
        return {
            name: round(sum(session[key][name] for session in sessions) / len(sessions), 6)
            for name in _AUDIT_METRICS
        }
    automatic = macro("automatic_baseline")
    assisted = macro("human_assisted_overlay")
    return {
        "format": BATCH_FORMAT,
        "session_count": len(sessions),
        "annotated_observation_count": sum(
            session["annotated_observation_count"] for session in sessions
        ),
        "human_confirmed_count": sum(
            session["human_confirmed_count"] for session in sessions
        ),
        "third_reviewer_resolution_session_count": sum(
            session["review_decision_provenance"] == "third_reviewer_resolution"
            for session in sessions
        ),
        "two_reviewer_consensus_session_count": sum(
            session["review_decision_provenance"] == "two_reviewer_consensus"
            for session in sessions
        ),
        "unresolved_reviewer_disagreement_count": sum(
            session["unresolved_reviewer_disagreement_count"] for session in sessions
        ),
        "session_macro_automatic_baseline": automatic,
        "session_macro_human_assisted_overlay": assisted,
        "session_macro_assisted_minus_automatic": {
            name: round(assisted[name] - automatic[name], 6)
            for name in _AUDIT_METRICS
        },
        "minimum_automatic_interruption_rejection": min(
            session["automatic_baseline"]["interruption_rejection"]
            for session in sessions
        ),
        "local_numeric_thresholds": {
            "minimum_automatic_f1": 0.90,
            "maximum_automatic_contamination": 0.05,
            "observed_local_automatic_meets_both": (
                automatic["workflow_assignment_f1"] >= 0.90
                and automatic["cross_workflow_contamination"] <= 0.05
            ),
        },
        "sessions": sessions,
        "aggregation_unit": "independently_annotated_local_session",
        "preregistered_family_holdout_verified": False,
        "real_consenting_user_pilot_verified": False,
        "automatic_improvement_claim": False,
        "source_content_included": False,
    }


__all__ = [
    "prepare_review_packet", "apply_review", "score_review", "adjudicate_review",
    "resolve_review_disagreement", "provisional_activity_episodes",
    "EPISODE_FORMAT", "PACKET_FORMAT", "DECISION_FORMAT", "ADJUDICATION_FORMAT", "RESOLUTION_FORMAT", "BATCH_FORMAT",
    "aggregate_review_scores",
]
