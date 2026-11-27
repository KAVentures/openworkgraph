"""R23: regulated and service administration reference workflows.

These are deliberately recorded-evidence models, not statutory or clinical
policies. All deadlines are configurable elapsed hours and are not legal SLAs.
The existing domain-pack engine owns mappings, metrics, review and persistence.
"""
from __future__ import annotations

from .event_log_domain_packs_operations import (
    _role, _parameter, _time, _count, _rule, _deadline, _pack,
)


def _build(key, title, description, prefix, case, case_label, stages,
           clocks, counts, edges, unique, separation, assumptions):
    """Build a distinct, versioned reference path with explicit rule definitions.

    Clock rows: (parameter, label, default hours, trigger, response).
    Count rows: (metric suffix, activity). Edges and independence rows are
    separately declared; optional roles are resolved by the existing engine.
    """
    roles = [_role(role, label, required) for role, label, required in stages]
    role_ids = {r["role"] for r in roles}
    if len(role_ids) != len(roles):
        raise ValueError("Duplicate activity role in domain definition")
    referenced = {v for _, _, _, a, b in clocks for v in (a, b)}
    referenced |= {r for _, r in counts}
    referenced |= {v for a, b in (*edges, *separation) for v in (a, b)}
    referenced |= set(unique)
    if not referenced <= role_ids:
        raise ValueError("Unknown activity role in domain definition")
    params = [_parameter(name, label, hours) for name, label, hours, _, _ in clocks]
    metrics = [_time(case, prefix + "_" + a + "_to_" + b, a, b)
               for _, _, _, a, b in clocks]
    metrics += [_count(case, prefix + "_" + suffix, role) for suffix, role in counts]
    rules = [_deadline(case, a, b, name) for name, _, _, a, b in clocks]
    rules += [_rule(case, "precedence", a, b) for a, b in edges]
    rules += [_rule(case, "existence", role, min=1, max=1) for role in unique]
    rules += [_rule(case, "segregation_of_duties", a, b) for a, b in separation]
    return _pack(key, title, description, case, case_label, roles, params, metrics,
                 rules, assumptions)


REFERRAL_ADMINISTRATION = _build(
    "healthcare_referral_administration", "Healthcare referral administration",
    "Review recorded referral intake, administrative completeness, routing, "
    "scheduling and notification. This does not evaluate clinical decisions.",
    "refadmin", "referral_case", "Referral administration record",
    [
        ("received", "Record referral receipt", True),
        ("checked", "Record administrative completeness check", True),
        ("routed", "Record routing to receiving service", True),
        ("returned", "Record administrative query", False),
        ("scheduled", "Record appointment scheduling", True),
        ("notified", "Record scheduling notification", True),
        ("closed", "Close administration record", True),
    ],
    [
        ("check_hours", "Completeness check within (elapsed hours of receipt)", 24, "received", "checked"),
        ("route_hours", "Routing within (elapsed hours of receipt)", 48, "received", "routed"),
        ("schedule_hours", "Scheduling within (elapsed hours of routing)", 168, "routed", "scheduled"),
        ("notify_hours", "Notification within (elapsed hours of scheduling)", 24, "scheduled", "notified"),
    ],
    [("query_records", "returned"), ("schedule_records", "scheduled"),
     ("notification_records", "notified")],
    [("received", "checked"), ("checked", "routed"), ("routed", "returned"),
     ("routed", "scheduled"), ("scheduled", "notified"), ("notified", "closed")],
    ["received", "closed"], [],
    [
        "Use an administrative referral identifier, not a patient identifier. "
        "Only select accepted referrals following the scheduling reference path.",
        "Urgent triage, refusals, clinical prioritization, patient contact outcomes, waiting-list "
        "policy, medical appropriateness and statutory guarantees are outside this model.",
        "A notification record does not establish that the patient received or understood a message. "
        "No health outcome, patient safety determination or treatment authorization is inferred.",
    ],
)

PERMIT_ADMINISTRATION = _build(
    "public_permit_administration", "Public permit administration",
    "Track a selected permit application from filing to recorded decision, notification "
    "and archival; inspect requests for additional information.",
    "permitadm", "permit_case", "Permit application record",
    [
        ("filed", "Record application filing", True),
        ("screened", "Record completeness screening", True),
        ("supplement", "Record additional information request", False),
        ("assessed", "Record assessment completed", True),
        ("reviewed", "Record independent review", False),
        ("decided", "Record decision", True),
        ("notified", "Record decision notification", True),
        ("archived", "Record archival", True),
    ],
    [
        ("screen_hours", "Screening within (elapsed hours of filing)", 72, "filed", "screened"),
        ("assessment_hours", "Assessment within (elapsed hours of screening)", 336, "screened", "assessed"),
        ("decision_hours", "Decision within (elapsed hours of assessment)", 168, "assessed", "decided"),
        ("notice_hours", "Notification within (elapsed hours of decision)", 48, "decided", "notified"),
    ],
    [("supplement_requests", "supplement"), ("decision_records", "decided"),
     ("review_records", "reviewed")],
    [("filed", "screened"), ("screened", "supplement"), ("screened", "assessed"),
     ("assessed", "reviewed"), ("assessed", "decided"), ("reviewed", "decided"),
     ("decided", "notified"), ("notified", "archived")],
    ["filed", "decided", "archived"], [("assessed", "reviewed")],
    [
        "The reference represents one decided application, not appeals, withdrawals, renewals "
        "or multiple jurisdictional decisions.",
        "Clock suspension during applicant response or statutory business-day counting is not modeled.",
        "Decision and notice labels do not establish legal validity, service of notice, eligibility "
        "or the independence of a person behind a recorded resource identifier.",
    ],
)

CONSTRUCTION_RFI = _build(
    "construction_rfi_administration", "Construction information request administration",
    "Review requests for information from submission to recorded response and closure, "
    "including clarification and independent review milestones.",
    "rfiadm", "rfi_case", "Request for information",
    [
        ("submitted", "Record information request submission", True),
        ("assigned", "Record assignment", True),
        ("clarification", "Record clarification request", False),
        ("drafted", "Record draft response", True),
        ("reviewed", "Record response review", False),
        ("issued", "Record response issued", True),
        ("acknowledged", "Record recipient acknowledgement", False),
        ("closed", "Close information request", True),
    ],
    [
        ("assign_hours", "Assignment within (elapsed hours of submission)", 24, "submitted", "assigned"),
        ("draft_hours", "Draft response within (elapsed hours of assignment)", 72, "assigned", "drafted"),
        ("issue_hours", "Response issue within (elapsed hours of draft)", 48, "drafted", "issued"),
        ("close_hours", "Closure within (elapsed hours of issue)", 168, "issued", "closed"),
    ],
    [("clarification_records", "clarification"), ("response_records", "issued"),
     ("acknowledgement_records", "acknowledged")],
    [("submitted", "assigned"), ("assigned", "clarification"), ("assigned", "drafted"),
     ("drafted", "reviewed"), ("drafted", "issued"), ("reviewed", "issued"),
     ("issued", "acknowledged"), ("issued", "closed")],
    ["submitted", "closed"], [("drafted", "reviewed")],
    [
        "A response record is not a design approval, change order, safety approval, "
        "instruction to proceed or verified contractual notice.",
        "Select one ordinary issued-response path; multi-party reviews, revised responses, "
        "disputed requests and canceled requests can deviate legitimately.",
        "Actual construction progress, delay damages and responsibility are not measured.",
    ],
)

CUSTOMS_DOCUMENTATION = _build(
    "customs_documentation_administration", "Customs documentation administration",
    "Review administrative preparation, filing, recorded external response and file closure "
    "for a selected shipment declaration.",
    "customsadm", "declaration_case", "Declaration administration record",
    [
        ("opened", "Open declaration file", True),
        ("documents", "Record documents collected", True),
        ("checked", "Record documentation check", False),
        ("filed", "Record declaration filing", True),
        ("query", "Record external query", False),
        ("response", "Record external disposition", True),
        ("archived", "Record file archival", True),
    ],
    [
        ("documents_hours", "Documents within (elapsed hours of opening)", 48, "opened", "documents"),
        ("filing_hours", "Filing within (elapsed hours of documents)", 24, "documents", "filed"),
        ("response_hours", "Disposition record within (elapsed hours of filing)", 72, "filed", "response"),
        ("archive_hours", "Archival within (elapsed hours of disposition)", 48, "response", "archived"),
    ],
    [("query_records", "query"), ("filing_records", "filed"), ("check_records", "checked")],
    [("opened", "documents"), ("documents", "checked"), ("documents", "filed"),
     ("checked", "filed"), ("filed", "query"), ("filed", "response"), ("response", "archived")],
    ["opened", "archived"], [("documents", "checked")],
    [
        "A recorded disposition does not establish clearance, release authority, tariff classification, "
        "sanctions screening, customs compliance or physical delivery.",
        "Amendments, multiple filings, inspections, holds and split shipments can require "
        "a different reference model.",
        "The external response clock is descriptive and includes external waiting; it is not "
        "an attribution of internal delay or employee performance.",
    ],
)

ADMISSIONS_ADMINISTRATION = _build(
    "education_admissions_administration", "Education admissions administration",
    "Inspect recorded application intake, completeness, review, decision communication and "
    "administrative closure without judging admission eligibility.",
    "admitadm", "application_case", "Admission application record",
    [
        ("submitted", "Record application submission", True),
        ("checked", "Record completeness review", True),
        ("query", "Record missing-information request", False),
        ("assessed", "Record assessment completed", True),
        ("reviewed", "Record second review", False),
        ("decided", "Record decision", True),
        ("communicated", "Record decision communication", True),
        ("closed", "Close application administration", True),
    ],
    [
        ("check_hours", "Completeness review within (elapsed hours of submission)", 72, "submitted", "checked"),
        ("assessment_hours", "Assessment within (elapsed hours of check)", 336, "checked", "assessed"),
        ("decision_hours", "Decision record within (elapsed hours of assessment)", 168, "assessed", "decided"),
        ("communication_hours", "Communication within (elapsed hours of decision)", 48, "decided", "communicated"),
    ],
    [("query_records", "query"), ("decision_records", "decided"), ("second_reviews", "reviewed")],
    [("submitted", "checked"), ("checked", "query"), ("checked", "assessed"),
     ("assessed", "reviewed"), ("assessed", "decided"), ("reviewed", "decided"),
     ("decided", "communicated"), ("communicated", "closed")],
    ["submitted", "decided", "closed"], [("assessed", "reviewed")],
    [
        "Select one ordinary decided application. Waitlists, deferments, withdrawals, "
        "appeals and multiple program choices can legitimately deviate.",
        "Do not map personal applicant characteristics into this template. It measures "
        "administrative events, not admission suitability, ranking or fairness.",
        "A decision record and a communication record do not verify the decision's content, "
        "receipt or enrollment.",
    ],
)

GRANT_ADMINISTRATION = _build(
    "research_grant_administration", "Research grant administration",
    "Review recorded proposal handling, award setup, milestone reporting and administrative "
    "closeout for a selected awarded grant.",
    "grantadm", "grant_case", "Award administration record",
    [
        ("submitted", "Record proposal submission", True),
        ("reviewed", "Record administrative review", False),
        ("awarded", "Record award notification", True),
        ("setup", "Record award setup", True),
        ("report", "Record milestone report", True),
        ("reconciled", "Record closeout reconciliation", False),
        ("closed", "Record administrative closeout", True),
    ],
    [
        ("review_hours", "Review within (elapsed hours of submission)", 168, "submitted", "reviewed"),
        ("setup_hours", "Award setup within (elapsed hours of award)", 168, "awarded", "setup"),
        ("report_hours", "First report within (elapsed hours of setup)", 2160, "setup", "report"),
        ("close_hours", "Closeout within (elapsed hours of report)", 720, "report", "closed"),
    ],
    [("report_records", "report"), ("award_records", "awarded"),
     ("reconciliation_records", "reconciled")],
    [("submitted", "reviewed"), ("submitted", "awarded"), ("reviewed", "awarded"),
     ("awarded", "setup"), ("setup", "report"), ("report", "reconciled"),
     ("report", "closed"), ("reconciled", "closed")],
    ["submitted", "awarded", "closed"], [("report", "reconciled")],
    [
        "Select awards with one selected reporting/closeout cycle; the reference permits "
        "one report and repeated reports may be legitimate deviations.",
        "The report-to-close deadline applies to every recorded report under the existing "
        "response rule; it is not a contractual deadline calculation.",
        "Award, reconciliation and closure records do not verify funding eligibility, "
        "payment, allowable costs, research integrity or financial compliance.",
    ],
)

REGULATED_SERVICE_PACKS = {
    p["pack_id"]: {p["version"]: p}
    for p in (REFERRAL_ADMINISTRATION, PERMIT_ADMINISTRATION, CONSTRUCTION_RFI,
              CUSTOMS_DOCUMENTATION, ADMISSIONS_ADMINISTRATION, GRANT_ADMINISTRATION)
}
