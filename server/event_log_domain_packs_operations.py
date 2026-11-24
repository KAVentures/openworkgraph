"""Versioned operational-control domain packs (roadmap #370 R23).

All definitions run on the existing local event-log workspace. Targets are
illustrative elapsed-time assumptions, not policy or source-verified outcomes.
"""
from __future__ import annotations

FORMAT = "openworkgraph.domain-pack.v1"


def _pack(pack_id, title, description, case_role, case_label, required, optional,
          sequence, targets, durations, counts, precedence, existence, assumptions):
    """Build an ordinary R23 definition from an explicit, reviewable role list."""
    case = "@" + case_role
    roles = dict(required + optional)
    assert len(roles) == len(required) + len(optional)
    assert set(sequence) <= set(roles)
    required_roles = dict(required)
    metric_prefix = {"manufacturing_quality": "mfg_quality",
                     "supplier_qualification": "supplier_qual",
                     "financial_close": "fin_close"}[pack_id]
    metrics = [
        {"case_type": case, "metric": {
            "name": metric_prefix + "_" + name, "kind": "time_between",
            "from_activity": "@" + a, "to_activity": "@" + b}}
        for name, a, b in durations
    ] + [
        {"case_type": case, "metric": {
            "name": metric_prefix + "_" + name, "kind": "activity_count",
            "activity": "@" + a}}
        for name, a in counts
    ]
    compliance = [
        {"case_type": case, "rule": {
            "template": "response_within", "a": "@" + a, "b": "@" + b,
            "seconds": "$" + name}}
        for name, _, _, _, _, a, b in targets
    ] + [
        {"case_type": case, "rule": {
            "template": "precedence", "a": "@" + a, "b": "@" + b}}
        for a, b in precedence
    ] + [
        {"case_type": case, "rule": {
            "template": "existence", "a": "@" + a, **limits}}
        for a, limits in existence
    ]
    return {
        "format": FORMAT, "pack_id": pack_id, "version": "1.0.0",
        "title": title, "reference_goal": title + " example lifecycle",
        "description": description,
        "object_types": [{"role": case_role, "label": case_label, "required": True}],
        "activities": [{"role": role, "label": label, "required": role in required_roles}
                       for role, label in roles.items()],
        "parameters": [{"name": name, "label": label, "unit": unit, "default": default,
                        "min": 0.25 if unit == "hours" else 1, "max": maximum}
                       for name, label, unit, default, maximum, _, _ in targets],
        "metrics": metrics, "compliance": compliance, "object_rules": [],
        "reference_model": {
            "types": [{"object_type": case, "steps": [
                "@" + role if role in required_roles else {"optional": "@" + role}
                for role in sequence]}],
            "arcs": [{"activity": "@" + role, "object_type": case, "variable": False}
                     for role in sequence],
        },
        "assumptions": [
            "Each case is one record in one declared source/account scope. "
            "Do not combine colliding record IDs across tenants.",
            "The selected log is the observed population. Partial or delayed exports "
            "do not prove that a case is overdue, incomplete or abandoned.",
            "Targets are configurable elapsed wall-clock windows, not business-day SLAs, "
            "adopted policy, contract terms or legal requirements.",
            "The reference model is one example sequence. Exceptions and repeated steps "
            "can be legitimate and require human review.",
            "Optional unmapped roles are not applicable, not proof an event never happened.",
            "This descriptive pack never authorizes source access, business actions, "
            "employee ranking, export or automated decisions.",
            *assumptions,
        ],
    }


MANUFACTURING_QUALITY_1_0_0 = _pack(
    "manufacturing_quality", "Manufacturing nonconformance",
    "Review nonconformance intake, containment, investigation, corrective action, "
    "verification and closure; flag delayed or repeated recorded steps.",
    "nonconformance", "Nonconformance record",
    required=[("reported", "Report nonconformance"), ("investigated", "Record investigation"),
              ("disposition", "Record disposition"), ("closed", "Close record")],
    optional=[("contained", "Record containment"), ("corrective_action", "Record corrective action"),
              ("verified", "Record effectiveness verification"), ("reopened", "Reopen record")],
    sequence=["reported", "contained", "investigated", "disposition",
              "corrective_action", "verified", "closed"],
    targets=[("investigation_hours", "Investigation within (elapsed hours)", "hours", 48, 8760,
              "reported", "investigated"),
             ("closure_days", "Closure within (elapsed days)", "days", 30, 730,
              "reported", "closed")],
    durations=[("report_to_investigation", "reported", "investigated"),
               ("report_to_close", "reported", "closed"),
               ("action_to_verification", "corrective_action", "verified")],
    counts=[("corrective_actions", "corrective_action"), ("reopens", "reopened"),
            ("dispositions", "disposition")],
    precedence=[("reported", "contained"), ("reported", "investigated"),
                ("investigated", "disposition"), ("disposition", "closed"),
                ("corrective_action", "verified")],
    existence=[("closed", {"min": 1, "max": 1}), ("reopened", {"max": 0})],
    assumptions=[
        "A case is one nonconformance record, not a production lot or individual defect.",
        "Containment and corrective action are optional because disposition can follow "
        "different approved paths. A closure event is not independent proof of remediation.",
        "No product safety, release decision, regulatory quality compliance or CAPA effectiveness "
        "is inferred from the sequence; a qualified reviewer evaluates actual evidence.",
    ],
)

SUPPLIER_QUALIFICATION_1_0_0 = _pack(
    "supplier_qualification", "Supplier qualification operations",
    "Review recorded qualification requests, checks, decisions and optional activation; "
    "identify rework, timing gaps and unusual recorded order.",
    "qualification", "Supplier qualification request",
    required=[("requested", "Request qualification"), ("checked", "Record qualification checks"),
              ("decision", "Record qualification decision")],
    optional=[("documents_requested", "Request documents"), ("documents_received", "Receive documents"),
              ("activated", "Record activation"), ("reopened", "Reopen request")],
    sequence=["requested", "documents_requested", "documents_received", "checked", "decision", "activated"],
    targets=[("check_days", "Checks within (elapsed days)", "days", 10, 365, "requested", "checked"),
             ("decision_days", "Decision within (elapsed days)", "days", 20, 730, "requested", "decision")],
    durations=[("request_to_check", "requested", "checked"),
               ("request_to_decision", "requested", "decision"),
               ("documents_turnaround", "documents_requested", "documents_received")],
    counts=[("document_requests", "documents_requested"), ("reopens", "reopened"),
            ("decisions", "decision")],
    precedence=[("requested", "checked"), ("checked", "decision"),
                ("documents_requested", "documents_received"), ("decision", "activated")],
    existence=[("decision", {"min": 1, "max": 1}), ("reopened", {"max": 0}),
               ("activated", {"max": 1})],
    assumptions=[
        "A case is one qualification request, not a supplier's lifetime record.",
        "Decision is a recorded event regardless of its outcome. A decision followed by "
        "activation does not prove the decision was positive or activation was authorized.",
        "No supplier risk, sanctions, eligibility, contractual approval or actual account "
        "activation is established. Supplier names and document contents are not required.",
    ],
)

FINANCIAL_CLOSE_1_0_0 = _pack(
    "financial_close", "Financial close task controls",
    "Review period-close task assignments, recorded preparation and review, closure, "
    "adjustments and reopenings without accessing accounting balances.",
    "close_task", "Financial close task",
    required=[("assigned", "Assign close task"), ("prepared", "Record preparation"),
              ("reviewed", "Record review"), ("closed", "Close task")],
    optional=[("adjustment", "Record adjustment"), ("reopened", "Reopen task")],
    sequence=["assigned", "prepared", "adjustment", "reviewed", "closed"],
    targets=[("preparation_hours", "Preparation within (elapsed hours)", "hours", 48, 8760,
              "assigned", "prepared"),
             ("closure_days", "Closure within (elapsed days)", "days", 7, 365, "assigned", "closed")],
    durations=[("assignment_to_preparation", "assigned", "prepared"),
               ("preparation_to_review", "prepared", "reviewed"),
               ("assignment_to_close", "assigned", "closed")],
    counts=[("adjustments", "adjustment"), ("reopens", "reopened"), ("reviews", "reviewed")],
    precedence=[("assigned", "prepared"), ("prepared", "reviewed"),
                ("reviewed", "closed"), ("prepared", "adjustment")],
    existence=[("closed", {"min": 1, "max": 1}), ("reopened", {"max": 0})],
    assumptions=[
        "A case is one close task scoped to a reporting entity and period, not a whole ledger.",
        "A recorded review is not proof of accounting accuracy, independent approval or "
        "period close. Revisions and reopening may be necessary.",
        "This pack does not validate balances, reconciliations, accounting standards, "
        "financial statements or audit controls and cannot post a journal entry.",
    ],
)
# Use the existing segregation-of-duties rule. Missing recorded resources
# yield unknown under the engine's contract, not a verified independent review.
FINANCIAL_CLOSE_1_0_0["compliance"].append({
    "case_type": "@close_task", "rule": {
        "template": "segregation_of_duties", "a": "@prepared", "b": "@reviewed"},
})
