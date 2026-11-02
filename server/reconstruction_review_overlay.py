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
    eligible = []
    for entry in projected["ambiguous_assignment_review_queue"]:
        eligible.append({
            "event_id": entry["event_id"],
            "candidate_items": [
                {"item_id": target["item_id"], "weak_relations": target["weak_relations"]}
                for target in entry["candidate_items"]
            ],
            "candidate_items_truncated": entry["candidate_items_truncated"],
        })
    packet = {
        "format": PACKET_FORMAT,
        "evidence_sha256": digest,
        "admitted_observation_count": len(linked["rows"]),
        "linked_item_count": len(groups),
        "eligible_review_total": projected["ambiguous_assignment_review_total"],
        "eligible_review_truncated": projected["ambiguous_assignment_review_truncated"],
        "eligible_candidates": eligible,
        "source_content_included": False,
        "canonical_membership_unchanged": True,
    }
    return packet, linked, groups


def prepare_review_packet(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce only opaque IDs and typed candidate bases; never raw work text."""
    return _snapshot(events)[0]


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
        entry["event_id"]: {target["item_id"] for target in entry["candidate_items"]}
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
    rejected: list[dict[str, str]] = []
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
        if action == "confirm":
            confirmations[event_id] = item_id
            reviewed[item_id].append(event_id)
        else:
            rejected.append({"event_id": event_id, "item_id": item_id})
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
            "automatic_member_count": len(members) - len(confirmed),
            "membership_status": "human_reviewed_overlay" if confirmed else "automatic_evidence",
        })
    return {
        "format": OVERLAY_FORMAT,
        "evidence_sha256": expected["evidence_sha256"],
        "items": items,
        "human_confirmed_count": len(confirmations),
        "human_rejected_count": len(rejected),
        "human_rejections": rejected,
        "eligible_unreviewed_count": expected["eligible_review_total"] - len(seen),
        "eligible_review_truncated": expected["eligible_review_truncated"],
        "unassigned_event_ids": sorted(singleton_ids - set(confirmations), key=position.__getitem__),
        "canonical_membership_unchanged": True,
        "human_decisions_are_not_source_evidence": True,
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
        "automatic_baseline": scored(groups),
        "human_assisted_overlay": scored([item["event_ids"] for item in overlay["items"]]),
        "complete_local_annotations_required": True,
        "preregistered_family_holdout": False,
        "automatic_model_improvement_claim": False,
        "real_pilot_validated": False,
    }


__all__ = [
    "prepare_review_packet", "apply_review", "score_review",
    "PACKET_FORMAT", "DECISION_FORMAT",
]
