from __future__ import annotations

"""Provider-neutral, read-only Open Work case graph projection.

This is an ephemeral composition of independently sourced business state and
historical OWG observations. It never upgrades observed behavior to normative
policy, decides an invoice is owed, authorizes an action, stores new data, or
asserts a customer outcome on an agent's say-so.
"""
from datetime import datetime, timezone
import re
from typing import Any, Mapping

_ITEM = re.compile(r"^owg:w:[0-9a-f]{32}$")
_OBSERVED = re.compile(r"^owg:r:[0-9a-f]{32}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,48}$")
_STATES = {"open", "pending", "blocked", "in_progress", "closed", "done", "paid",
           "void", "uncollectible", "failed", "canceled", "unknown"}
_MAX_OBSERVED = 8
_MAX_EVENT_ITEMS = 40


class OpenWorkModelError(ValueError):
    pass


def _stamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if d.tzinfo is None:
        return None
    return d.astimezone(timezone.utc).isoformat()


def compose_case_graph(
    *,
    candidate: Mapping[str, Any],
    source: Mapping[str, Any],
    related_owg_evidence: list[dict[str, Any]] | None = None,
    approved_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a source-backed case and project a small typed evidence graph.

    Provider-specific content must already have been minimized by the source
    adapter. This function deliberately selects only safe, fixed-schema fields.
    No names, amounts, titles, free-text policy or native record identifiers
    are copied into the graph.
    """
    ref = candidate.get("work_item_ref")
    provider = source.get("provider")
    status = source.get("source_state")
    verified_at = _stamp(source.get("verified_at"))
    if not isinstance(ref, str) or not _ITEM.fullmatch(ref):
        raise OpenWorkModelError("invalid opaque work item reference")
    if not isinstance(provider, str) or not _TOKEN.fullmatch(provider):
        raise OpenWorkModelError("invalid source provider")
    if status not in _STATES or not verified_at:
        raise OpenWorkModelError("missing or unsupported source-reported state")

    nodes: list[dict[str, Any]] = [
        {"id": ref, "type": "business_case", "state": "candidate",
         "responsibility": "unknown", "business_goal_confirmed": False},
        {"id": "source:current", "type": "source_status_receipt",
         "provider": provider, "state": status,
         "verified_at": verified_at, "authority_scope": "provider_status_only"},
    ]
    edges: list[dict[str, Any]] = [
        {"from": "source:current", "to": ref, "type": "source_reports_state",
         "verified": True, "provenance": "authenticated_provider_read"},
    ]
    accepted: set[str] = set()
    for item in (related_owg_evidence or [])[:_MAX_OBSERVED]:
        if not isinstance(item, dict):
            continue
        resource = item.get("resource_ref")
        if (not isinstance(resource, str) or not _OBSERVED.fullmatch(resource)
            or resource in accepted or item.get("observed_event_count", 0) <= 0
            or item.get("recorded_behavior_not_policy") is not True):
            continue
        accepted.add(resource)
        nodes.append({
            "id": resource, "type": "observed_activity",
            "observed_event_count": min(100_000, int(item.get("observed_event_count") or 0)),
            "observed_session_count": min(100_000, max(0, int(item.get("observed_session_count") or 0))),
            "observed_actor_count": min(100_000, max(0, int(item.get("observed_actor_count") or 0))),
            "timeline": [
                {
                    "event_id": str(event.get("event_id") or "")[:120] if re.fullmatch(
                        r"[A-Za-z0-9:._-]{1,120}", str(event.get("event_id") or "")) else "",
                    "observed_at": _stamp(event.get("observed_at")),
                    "action": str(event.get("observed_action_type") or "unknown")[:48] if
                         re.fullmatch(r"[a-z0-9_:-]{1,48}", str(event.get("observed_action_type") or "")) else "unknown",
                }
                for event in (item.get("timeline") or [])[:_MAX_EVENT_ITEMS]
                if isinstance(event, dict)
            ],
            "proof_scope": "retained_human_or_agent_observations",
        })
        edges.append({
            "from": resource, "to": ref, "type": "observed_same_provider_record",
            "verified": False,
            "provenance": "exact_source_identity_and_retained_observation",
            "not_a_verified_business_handoff": True,
        })

    policy_id = None
    if approved_policy is not None:
        if (approved_policy.get("authoritatively_declared") is not True
                or not isinstance(approved_policy.get("policy_id"), str)
                or not _TOKEN.fullmatch(approved_policy["policy_id"])):
            raise OpenWorkModelError("policy must be explicitly declared and structurally identified")
        policy_id = approved_policy["policy_id"]
        nodes.append({"id": "policy:" + policy_id, "type": "declared_policy_reference",
                      "policy_id": policy_id, "policy_is_observed_behavior": False})
        edges.append({"from": "policy:" + policy_id, "to": ref, "type": "declared_rule_for_case",
                      "verified": True, "provenance": "owner_declared_policy_reference"})

    return {
        "format": "openworkgraph.business-case-graph.v1",
        "case_ref": ref,
        "nodes": nodes,
        "edges": edges,
        "source_status_reported": True,
        "source_status_verified_at": verified_at,
        "business_obligation_verified": False,
        "customer_outcome_verified": False,
        "operating_policy_present": policy_id is not None,
        "related_observation_count": len(accepted),
        "unknowns": [
            "customer_outcome", "case_responsible_actor", "business_obligation",
            *([] if policy_id else ["approved_company_procedure"]),
        ],
        "authorization": {
            "agent_may_execute": False,
            "may_contact_customer": False,
            "historical_actions_grant_permission": False,
        },
        "data_handling": {
            "source_ids_included": False,
            "customer_names_included": False,
            "amounts_included": False,
            "persisted_as_graph": False,
        },
    }


__all__ = ["compose_case_graph", "OpenWorkModelError"]
