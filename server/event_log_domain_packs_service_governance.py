"""R23 operational governance packs for explicitly selected event logs.

These definitions are illustrative, versioned and read-only. They do not decide
legal obligations, approve changes, judge employees or authorize source access.
"""
from __future__ import annotations

from typing import Any, Sequence

FORMAT = "openworkgraph.domain-pack.v1"


def _pack(pack_id: str, title: str, description: str, case_role: str,
          case_label: str, roles: list[tuple[str, str, bool]],
          targets: list[tuple[str, str, float, float, float, str]],
          times: list[tuple[str, str, str]], counts: list[tuple[str, str]],
          sequences: list[tuple[str, str]],
          deadlines: list[tuple[str, str, str]],
          cardinalities: list[tuple[str, int, int]],
          steps: list[str | dict[str, str]], assumptions: list[str],
          segregation: Sequence[tuple[str, str]] = ()) -> dict[str, Any]:
    """Build the existing pack schema without changing resolution or storage."""
    case_type = "@" + case_role
    prefix = {"change_control": "chg", "customer_complaints": "complaint",
              "data_access_requests": "dar", "warehouse_fulfillment": "whf",
              "sales_quote_approval": "sqa", "payment_disputes": "pdis"}[pack_id]
    def rule(template: str, **fields: Any) -> dict[str, Any]:
        return {"case_type": case_type, "rule": {"template": template, **fields}}

    def metric(name: str, kind: str, **fields: Any) -> dict[str, Any]:
        return {"case_type": case_type,
                "metric": {"name": prefix + "_" + name, "kind": kind, **fields}}

    step_roles = [step if isinstance(step, str) else step["optional"] for step in steps]
    return {
        "format": FORMAT, "pack_id": pack_id, "version": "1.0.0",
        "title": title, "description": description,
        "reference_goal": title + " illustrative recorded lifecycle",
        "object_types": [{"role": case_role, "label": case_label, "required": True}],
        "activities": [{"role": role, "label": label, "required": required}
                       for role, label, required in roles],
        "parameters": [{"name": name, "label": label, "default": default,
                        "min": low, "max": high, "unit": unit}
                       for name, label, default, low, high, unit in targets],
        "metrics": [
            metric(name, "time_between", from_activity="@" + start, to_activity="@" + end)
            for name, start, end in times
        ] + [metric(name, "activity_count", activity="@" + activity)
             for name, activity in counts],
        "compliance": [
            rule("precedence", a="@" + start, b="@" + end) for start, end in sequences
        ] + [
            rule("response_within", a="@" + start, b="@" + end, seconds="$" + target)
            for start, end, target in deadlines
        ] + [
            rule("existence", a="@" + activity, min=minimum, max=maximum)
            for activity, minimum, maximum in cardinalities
        ] + [
            rule("segregation_of_duties", a="@" + start, b="@" + end)
            for start, end in segregation
        ],
        "object_rules": [],
        "reference_model": {
            "types": [{"object_type": case_type, "steps": steps}],
            "arcs": [{"activity": role, "object_type": case_type, "variable": False}
                     for role in step_roles],
        },
        "assumptions": [
            "Each retained case is one declared source record in the selected population. "
            "The mapping is a user assertion, not a verified source identity.",
            "All times are elapsed wall-clock time. Time metrics use first recorded "
            "occurrences; response checks examine each initiating event.",
            "Targets are illustrative editable review thresholds, not adopted policy, "
            "legal requirements or contractual service levels.",
            "Missing, late or repeated records flag the supplied history for review. "
            "Open, incomplete, cancelled or exceptional cases may legitimately deviate.",
            "The reference lifecycle is illustrative, not an executable policy. "
            "Installation grants no source access, execution authority or verified outcomes.",
            *assumptions,
        ],
    }


CHANGE_CONTROL_1_0_0 = _pack(
    "change_control", "IT change control",
    "Inspect the recorded request, assessment, approval, implementation and validation "
    "of selected planned changes; review elapsed times, rework and separation of duties.",
    "change", "Change request",
    [
        ("requested", "Request change", True),
        ("assessed", "Assess change", True),
        ("approved", "Record approval", True),
        ("implemented", "Record implementation", True),
        ("validated", "Record validation", True),
        ("reworked", "Return for rework", False),
        ("rollback", "Record rollback", False),
        ("closed", "Close change", False),
    ],
    [
        ("assessment_hours", "Assessment within (elapsed hours)", 48, .25, 8760, "hours"),
        ("validation_hours", "Validation within (elapsed hours of implementation)", 24, .25, 8760, "hours"),
    ],
    [
        ("request_to_assessment", "requested", "assessed"),
        ("assessment_to_approval", "assessed", "approved"),
        ("approval_to_implementation", "approved", "implemented"),
        ("implementation_to_validation", "implemented", "validated"),
        ("request_to_validation", "requested", "validated"),
    ],
    [("rework_events", "reworked"), ("rollback_events", "rollback")],
    [("requested", "assessed"), ("assessed", "approved"),
     ("approved", "implemented"), ("implemented", "validated"),
     ("validated", "closed"), ("implemented", "rollback")],
    [("requested", "assessed", "assessment_hours"),
     ("implemented", "validated", "validation_hours")],
    [("implemented", 0, 1)],
    ["@requested", "@assessed", "@approved", "@implemented", "@validated",
     {"optional": "@closed"}],
    [
        "Scope this pack to planned changes with the stated lifecycle. Emergency, "
        "rejected, deferred and standard pre-approved changes need separately reviewed mappings.",
        "An approval event is not approval validity, an implementation event is not a successful "
        "deployment, and validation does not independently prove production health.",
        "Repeated implementation may represent a legitimate retry or phased rollout. "
        "Rollback/rework are observed event counts, not failure or worker scores.",
        "Separation of duties compares recorded resources for request and approval; "
        "unknown resources do not prove independence.",
    ],
    segregation=[("requested", "approved")],
)

CUSTOMER_COMPLAINTS_1_0_0 = _pack(
    "customer_complaints", "Customer complaint resolution",
    "Review intake, acknowledgement, investigation, recorded resolution and communication "
    "for selected complaint cases, with explicit rework and escalation counts.",
    "complaint", "Complaint case",
    [
        ("received", "Receive complaint", True),
        ("acknowledged", "Acknowledge complaint", True),
        ("investigated", "Investigate complaint", True),
        ("resolution_recorded", "Record resolution", True),
        ("response_sent", "Record response sent", True),
        ("escalated", "Escalate complaint", False),
        ("reopened", "Reopen complaint", False),
        ("closed", "Close complaint", False),
    ],
    [
        ("acknowledgement_hours", "Acknowledgement within (elapsed hours)", 24, .25, 8760, "hours"),
        ("response_days", "Response within (elapsed days of intake)", 14, 1, 730, "days"),
    ],
    [
        ("intake_to_acknowledgement", "received", "acknowledged"),
        ("intake_to_resolution", "received", "resolution_recorded"),
        ("intake_to_response", "received", "response_sent"),
        ("resolution_to_response", "resolution_recorded", "response_sent"),
    ],
    [("escalations", "escalated"), ("reopens", "reopened")],
    [("received", "acknowledged"), ("received", "investigated"),
     ("investigated", "resolution_recorded"), ("resolution_recorded", "response_sent"),
     ("response_sent", "closed")],
    [("received", "acknowledged", "acknowledgement_hours"),
     ("received", "response_sent", "response_days")],
    [("reopened", 0, 0)],
    ["@received", "@acknowledged", "@investigated", "@resolution_recorded",
     "@response_sent", {"optional": "@closed"}],
    [
        "A recorded response is not proof of delivery, customer satisfaction, "
        "correct remediation or an upheld complaint.",
        "The reference sequence permits one investigation and one response; additional "
        "communications and escalation may be necessary and are not automatically errors.",
        "Reopening can represent appropriate follow-up. No complaint contents, customer "
        "identifiers beyond a local case alias, or sentiment data are required.",
    ],
)

DATA_ACCESS_REQUESTS_1_0_0 = _pack(
    "data_access_requests", "Data access request operations",
    "Review the operational handling of selected data requests from intake through "
    "verification, search, review and recorded response, without importing request contents.",
    "request", "Data request case",
    [
        ("received", "Receive request", True),
        ("verified", "Record verification", True),
        ("search_completed", "Record search completion", True),
        ("reviewed", "Record response review", True),
        ("response_sent", "Record response sent", True),
        ("clarification", "Request clarification", False),
        ("extension", "Record extension", False),
        ("reopened", "Reopen request", False),
        ("closed", "Close request", False),
    ],
    [
        ("verification_hours", "Verification within (elapsed hours)", 72, .25, 8760, "hours"),
        ("response_days", "Response review target (elapsed days of intake)", 21, 1, 730, "days"),
    ],
    [
        ("intake_to_verification", "received", "verified"),
        ("verification_to_search", "verified", "search_completed"),
        ("search_to_review", "search_completed", "reviewed"),
        ("intake_to_response", "received", "response_sent"),
    ],
    [("clarifications", "clarification"), ("extensions", "extension"), ("reopens", "reopened")],
    [("received", "verified"), ("verified", "search_completed"),
     ("search_completed", "reviewed"), ("reviewed", "response_sent"),
     ("response_sent", "closed")],
    [("received", "verified", "verification_hours"),
     ("received", "response_sent", "response_days")],
    [("reopened", 0, 0)],
    ["@received", "@verified", "@search_completed", "@reviewed", "@response_sent",
     {"optional": "@closed"}],
    [
        "This is an operational review pack, not a GDPR or other legal compliance checker. "
        "No statutory deadline, identity validity, exemption or right of access is determined.",
        "Extensions, clarification and pauses are not subtracted from the elapsed targets. "
        "An applicable deadline must be determined independently by the authorized organization.",
        "Recorded verification, search and review do not establish their adequacy or "
        "the completeness, safety or delivery of a response.",
        "Do not import request text, identity documents, personal data or response payloads "
        "for this pack. Use local pseudonymous case IDs and selected timestamps and labels.",
    ],
)

WAREHOUSE_FULFILLMENT_1_0_0 = _pack(
    "warehouse_fulfillment", "Warehouse outbound fulfillment",
    "Review recorded release, picking, packing, shipment and carrier handoff "
    "for selected outbound fulfillment records, including exception activity.",
    "fulfillment", "Outbound fulfillment record",
    [
        ("released", "Release fulfillment", True),
        ("picked", "Record picking complete", True),
        ("packed", "Record packing complete", True),
        ("shipment_recorded", "Record shipment", True),
        ("carrier_handoff", "Record carrier handoff", True),
        ("stock_exception", "Record stock exception", False),
        ("repack", "Record repacking", False),
        ("closed", "Close fulfillment", False),
    ],
    [
        ("pick_hours", "Pick within (elapsed hours of release)", 24, .25, 8760, "hours"),
        ("handoff_hours", "Handoff within (elapsed hours of release)", 48, .25, 8760, "hours"),
    ],
    [
        ("release_to_pick", "released", "picked"),
        ("pick_to_pack", "picked", "packed"),
        ("pack_to_shipment", "packed", "shipment_recorded"),
        ("shipment_to_handoff", "shipment_recorded", "carrier_handoff"),
        ("release_to_handoff", "released", "carrier_handoff"),
    ],
    [("stock_exceptions", "stock_exception"), ("repacks", "repack")],
    [("released", "picked"), ("picked", "packed"), ("packed", "shipment_recorded"),
     ("shipment_recorded", "carrier_handoff"), ("carrier_handoff", "closed")],
    [("released", "picked", "pick_hours"), ("released", "carrier_handoff", "handoff_hours")],
    [("shipment_recorded", 0, 1)],
    ["@released", "@picked", "@packed", "@shipment_recorded", "@carrier_handoff",
     {"optional": "@closed"}],
    [
        "Use one outbound fulfillment or shipment record as the case, not an entire "
        "order spanning several independently shipped parcels.",
        "The reference sequence is illustrative; split shipments, backorders and "
        "parallel pick tasks need separate models or object-centric review.",
        "A recorded carrier handoff does not prove delivery, correct inventory, "
        "physical custody or absence of damage.",
    ],
)

SALES_QUOTE_APPROVAL_1_0_0 = _pack(
    "sales_quote_approval", "Sales quote approval",
    "Review preparation, commercial review, approval and recorded issue of "
    "selected sales quotes, including revision and return-for-review activity.",
    "quote", "Sales quote record",
    [
        ("requested", "Receive quote request", True),
        ("prepared", "Prepare quote", True),
        ("reviewed", "Record commercial review", True),
        ("approved", "Record quote approval", True),
        ("issued", "Record quote issued", True),
        ("revised", "Revise quote", False),
        ("returned", "Return for review", False),
        ("closed", "Close quote record", False),
    ],
    [
        ("preparation_hours", "Prepare within (elapsed hours of request)", 24, .25, 8760, "hours"),
        ("issue_hours", "Issue within (elapsed hours of request)", 72, .25, 8760, "hours"),
    ],
    [
        ("request_to_preparation", "requested", "prepared"),
        ("preparation_to_review", "prepared", "reviewed"),
        ("review_to_approval", "reviewed", "approved"),
        ("approval_to_issue", "approved", "issued"),
        ("request_to_issue", "requested", "issued"),
    ],
    [("revisions", "revised"), ("returns", "returned")],
    [("requested", "prepared"), ("prepared", "reviewed"), ("reviewed", "approved"),
     ("approved", "issued"), ("issued", "closed")],
    [("requested", "prepared", "preparation_hours"), ("requested", "issued", "issue_hours")],
    [("issued", 0, 1)],
    ["@requested", "@prepared", "@reviewed", "@approved", "@issued",
     {"optional": "@closed"}],
    [
        "Scope to quotes expected to use this approval lifecycle; declined, withdrawn, "
        "automatically approved or revised quotes may need different reviewed definitions.",
        "A recorded approval does not establish authorization limits, pricing correctness "
        "or commercial validity, and issuance does not establish acceptance or revenue.",
        "Separation of duties uses recorded resources; unknown resources do not prove independence.",
    ],
    segregation=[("prepared", "approved")],
)

PAYMENT_DISPUTES_1_0_0 = _pack(
    "payment_disputes", "Payment dispute operations",
    "Review recorded intake, triage, evidence preparation, response and outcome "
    "for selected payment dispute cases without reading payment or customer details.",
    "dispute", "Payment dispute case",
    [
        ("received", "Receive dispute", True),
        ("triaged", "Record dispute triage", True),
        ("evidence_prepared", "Record evidence preparation", True),
        ("response_submitted", "Record response submission", True),
        ("outcome_recorded", "Record dispute outcome", True),
        ("escalated", "Escalate dispute", False),
        ("reopened", "Reopen dispute", False),
        ("closed", "Close dispute", False),
    ],
    [
        ("triage_hours", "Triage within (elapsed hours of intake)", 24, .25, 8760, "hours"),
        ("response_days", "Response target (elapsed days of intake)", 7, 1, 730, "days"),
    ],
    [
        ("intake_to_triage", "received", "triaged"),
        ("triage_to_evidence", "triaged", "evidence_prepared"),
        ("evidence_to_response", "evidence_prepared", "response_submitted"),
        ("response_to_outcome", "response_submitted", "outcome_recorded"),
        ("intake_to_outcome", "received", "outcome_recorded"),
    ],
    [("escalations", "escalated"), ("reopens", "reopened")],
    [("received", "triaged"), ("triaged", "evidence_prepared"),
     ("evidence_prepared", "response_submitted"), ("response_submitted", "outcome_recorded"),
     ("outcome_recorded", "closed")],
    [("received", "triaged", "triage_hours"), ("received", "response_submitted", "response_days")],
    [("reopened", 0, 0)],
    ["@received", "@triaged", "@evidence_prepared", "@response_submitted", "@outcome_recorded",
     {"optional": "@closed"}],
    [
        "Select cases expected to have a submitted response. Accepted disputes, "
        "withdrawals, arbitration and exceptional paths need their own reviewed scope.",
        "Illustrative elapsed targets are not card-network, bank, contractual or legal "
        "deadlines. Determine applicable deadlines independently.",
        "Recorded outcomes do not establish actual funds movement, fraud, liability "
        "or recovery; no payment initiation or dispute submission is authorized.",
        "Use pseudonymous case IDs and activity labels; do not import card numbers, "
        "account details, customer communications or dispute evidence payloads.",
    ],
)


SERVICE_GOVERNANCE_PACKS = {
    p["pack_id"]: {p["version"]: p}
    for p in (CHANGE_CONTROL_1_0_0, CUSTOMER_COMPLAINTS_1_0_0, DATA_ACCESS_REQUESTS_1_0_0,
              WAREHOUSE_FULFILLMENT_1_0_0, SALES_QUOTE_APPROVAL_1_0_0, PAYMENT_DISPUTES_1_0_0)
}
