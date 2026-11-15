"""Reviewable insurance-claims operations pack for opted-in event logs (R23).

Operational timestamps are supplied evidence, not insurance policy, coverage,
claim eligibility, a verified settlement, or authority to make decisions.
No claimant identity, diagnosis, policy body, or claim contents are required.
"""

FORMAT = "openworkgraph.domain-pack.v1"

INSURANCE_CLAIMS_1_0_0 = {
    "format": FORMAT,
    "pack_id": "insurance_claims",
    "version": "1.0.0",
    "title": "Insurance claims operations",
    "reference_goal": "Claims intake, assessment and recorded decision lifecycle",
    "description": (
        "Review the recorded operations of selected insurance claims: intake-to-triage, "
        "triage-to-assessment and intake-to-decision times, information requests, "
        "reopened cases, repeated decisions, and optional payment/closure. "
        "This does not assess coverage, entitlement, fraud, fairness or settlement."
    ),
    "object_types": [
        {"role": "claim", "label": "Claim", "required": True},
    ],
    "activities": [
        {"role": "received", "label": "Receive claim", "required": True},
        {"role": "triaged", "label": "Triage claim", "required": True},
        {"role": "assessed", "label": "Assess claim", "required": True},
        {"role": "decision_recorded", "label": "Record decision", "required": True},
        {"role": "information_requested", "label": "Request more information", "required": False},
        {"role": "information_received", "label": "Receive requested information", "required": False},
        {"role": "reopened", "label": "Reopen claim", "required": False},
        {"role": "payment_recorded", "label": "Record payment", "required": False},
        {"role": "closed", "label": "Close claim", "required": False},
    ],
    "parameters": [
        {"name": "triage_hours", "label": "Triage within (elapsed hours)",
         "unit": "hours", "default": 48, "min": 0.25, "max": 8760},
        {"name": "decision_days", "label": "Decision within (elapsed days)",
         "unit": "days", "default": 30, "min": 1, "max": 730},
    ],
    "metrics": [
        {"case_type": "@claim", "metric": {
            "name": "claims_time_to_triage", "kind": "time_between",
            "from_activity": "@received", "to_activity": "@triaged"}},
        {"case_type": "@claim", "metric": {
            "name": "claims_triage_to_assessment", "kind": "time_between",
            "from_activity": "@triaged", "to_activity": "@assessed"}},
        {"case_type": "@claim", "metric": {
            "name": "claims_time_to_decision", "kind": "time_between",
            "from_activity": "@received", "to_activity": "@decision_recorded"}},
        {"case_type": "@claim", "metric": {
            "name": "claims_information_requests", "kind": "activity_count",
            "activity": "@information_requested"}},
        {"case_type": "@claim", "metric": {
            "name": "claims_reopen_events", "kind": "activity_count",
            "activity": "@reopened"}},
        {"case_type": "@claim", "metric": {
            "name": "claims_payment_records", "kind": "activity_count",
            "activity": "@payment_recorded"}},
    ],
    "compliance": [
        {"case_type": "@claim", "rule": {
            "template": "response_within", "a": "@received", "b": "@triaged",
            "seconds": "$triage_hours"}},
        {"case_type": "@claim", "rule": {
            "template": "response_within", "a": "@received", "b": "@decision_recorded",
            "seconds": "$decision_days"}},
        {"case_type": "@claim", "rule": {
            "template": "precedence", "a": "@received", "b": "@triaged"}},
        {"case_type": "@claim", "rule": {
            "template": "precedence", "a": "@triaged", "b": "@assessed"}},
        {"case_type": "@claim", "rule": {
            "template": "precedence", "a": "@assessed", "b": "@decision_recorded"}},
        {"case_type": "@claim", "rule": {
            "template": "existence", "a": "@decision_recorded", "min": 1, "max": 1}},
        {"case_type": "@claim", "rule": {
            "template": "precedence", "a": "@information_requested", "b": "@information_received"}},
        {"case_type": "@claim", "rule": {
            "template": "existence", "a": "@reopened", "max": 0}},
        {"case_type": "@claim", "rule": {
            "template": "precedence", "a": "@decision_recorded", "b": "@payment_recorded"}},
        {"case_type": "@claim", "rule": {
            "template": "existence", "a": "@payment_recorded", "max": 1}},
        {"case_type": "@claim", "rule": {
            "template": "precedence", "a": "@decision_recorded", "b": "@closed"}},
    ],
    "object_rules": [],
    "reference_model": {
        "types": [{"object_type": "@claim", "steps": [
            "@received", "@triaged", {"optional": "@information_requested"},
            {"optional": "@information_received"}, "@assessed",
            "@decision_recorded", {"optional": "@payment_recorded"},
            {"optional": "@closed"},
        ]}],
        "arcs": [
            {"activity": "@" + role, "object_type": "@claim", "variable": False}
            for role in (
                "received", "triaged", "assessed", "decision_recorded",
                "information_requested", "information_received",
                "payment_recorded", "closed",
            )
        ],
    },
    "assumptions": [
        "Each case represents one selected claim in one declared provider/account scope. "
        "Do not combine colliding source IDs across insurers or tenants.",
        "The supplied log and its selected cohort define the population. A partial, paused, "
        "or delayed export cannot establish that a claim is overdue, abandoned or unresolved.",
        "Triage and decision targets are configurable elapsed wall-clock windows, not "
        "verified insurance policy, law, contract terms, business-day SLAs or adopted policy.",
        "The reference model shows one example operational sequence, not an authorized "
        "claims decision procedure. Exceptional paths and appeals can be valid.",
        "One recorded decision is a review assumption. Corrections, appeals and "
        "independently reviewed decisions can require more; a repeated record is not fraud.",
        "An absent or repeated payment event does not establish an unpaid/duplicate bank "
        "transaction. Payment events do not prove independent settlement or entitlement.",
        "Information requests and receipts are optional mappings. Missing events are "
        "missing evidence, not proof a claimant withheld information.",
        "Reopening is flagged for operational review, never scored as worker failure "
        "or evidence of an invalid claims decision.",
        "No personally identifying claimant details, health information, insurance "
        "policy wording, decision contents or external source credentials are required.",
        "Installing this pack is purely descriptive. It never authorizes claims decisions, "
        "payments, source reads, employee ranking or execution.",
    ],
}
