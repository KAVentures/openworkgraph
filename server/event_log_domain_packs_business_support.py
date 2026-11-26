"""Business support process templates for mapped, retained event logs."""
from .event_log_domain_packs_operations import (
    _role, _parameter, _time, _count, _rule, _deadline, _pack,
)


def _collections():
    c="collection_case"
    return _pack(
        "receivables_collections","Accounts receivable collections",
        "Review collection initiation, assignment, recorded outreach, payment recording and closure of selected paid collection cases.",
        c,"Collection case",
        [_role("opened","Open collection case"),
         _role("assigned","Assign case"),
         _role("contacted","Record initial outreach"),
         _role("promise_recorded","Record payment promise",False),
         _role("dispute_review","Record dispute review",False),
         _role("payment_recorded","Record payment"),
         _role("closed","Close collection case"),
         _role("reopened","Reopen case",False)],
        [_parameter("assignment_hours","Assignment within (elapsed hours)",24),
         _parameter("outreach_hours","Initial outreach within (elapsed hours)",72),
         _parameter("closure_hours","Closure after payment within (elapsed hours)",48)],
        [_time(c,"ar_open_to_assignment","opened","assigned"),
         _time(c,"ar_assignment_to_contact","assigned","contacted"),
         _time(c,"ar_open_to_payment","opened","payment_recorded"),
         _time(c,"ar_payment_to_close","payment_recorded","closed"),
         _time(c,"ar_contact_to_promise","contacted","promise_recorded"),
         _count(c,"ar_outreach_records","contacted"),
         _count(c,"ar_dispute_reviews","dispute_review"),
         _count(c,"ar_reopen_records","reopened")],
        [_deadline(c,"opened","assigned","assignment_hours"),
         _deadline(c,"opened","contacted","outreach_hours"),
         _deadline(c,"payment_recorded","closed","closure_hours"),
         _rule(c,"precedence","opened","assigned"),
         _rule(c,"precedence","assigned","contacted"),
         _rule(c,"precedence","contacted","promise_recorded"),
         _rule(c,"precedence","opened","payment_recorded"),
         _rule(c,"precedence","payment_recorded","closed"),
         _rule(c,"precedence","closed","reopened"),
         _rule(c,"existence","closed",min=0,max=1)],
        ["Select collection cases following the paid-closure reference path; write-offs, debt cancellation, disputes, instalments and partial payments require a separately reviewed scope.",
         "Recorded payment is not proof of settlement, amount accuracy, debt validity or enforceability. No balance, payment instrument or contact contents are required.",
         "Outreach counts are recorded events, not recommended contact frequency or permission to contact a debtor. Applicable requirements and customer circumstances must be reviewed independently."])


def _access():
    c="access_case"
    return _pack(
        "employee_access_requests","Employee access request fulfillment",
        "Review request intake, assessment, approval, recorded provisioning and verification for approved access requests.",
        c,"Access request case",
        [_role("requested","Record access request"),
         _role("assessed","Record access assessment"),
         _role("approved","Record access approval"),
         _role("provisioned","Record provisioning"),
         _role("verified","Record verification"),
         _role("closed","Close access request",False),
         _role("reworked","Record rework",False)],
        [_parameter("assessment_hours","Assessment within (elapsed hours)",24),
         _parameter("provisioning_hours","Provisioning after approval within (elapsed hours)",48),
         _parameter("verification_hours","Verification after provisioning within (elapsed hours)",24)],
        [_time(c,"access_request_to_assessment","requested","assessed"),
         _time(c,"access_assessment_to_approval","assessed","approved"),
         _time(c,"access_approval_to_provision","approved","provisioned"),
         _time(c,"access_provision_to_verify","provisioned","verified"),
         _time(c,"access_request_to_verify","requested","verified"),
         _count(c,"access_provision_records","provisioned"),
         _count(c,"access_rework_records","reworked")],
        [_deadline(c,"requested","assessed","assessment_hours"),
         _deadline(c,"approved","provisioned","provisioning_hours"),
         _deadline(c,"provisioned","verified","verification_hours"),
         _rule(c,"precedence","requested","assessed"),
         _rule(c,"precedence","assessed","approved"),
         _rule(c,"precedence","approved","provisioned"),
         _rule(c,"precedence","provisioned","verified"),
         _rule(c,"precedence","verified","closed"),
         _rule(c,"existence","provisioned",min=0,max=1),
         _rule(c,"segregation_of_duties","requested","approved"),
         _rule(c,"segregation_of_duties","provisioned","verified")],
        ["Select ordinary approved access requests. Denials, emergency access, multi-system provisioning, renewals and partial fulfillment may require different mappings.",
         "An approval record is not proof of authorization validity; provisioning and verification labels do not establish actual privileges, least privilege or successful access.",
         "Recorded resource separation is not verified separation of authority. Missing resources are unknown. No credentials, tokens, account names or entitlements are needed.",
         "Do not grant, revoke or otherwise modify access based on this report."])


def _recruitment():
    c="recruitment_case"
    return _pack(
        "recruitment_offer_operations","Recruitment offer operations",
        "Review administrative milestones for selected accepted-offer cases: intake, screening, interview, offer approval, offer recording and acceptance recording.",
        c,"Accepted-offer workflow",
        [_role("received","Record application intake"),
         _role("screened","Record screening"),
         _role("interviewed","Record interview"),
         _role("offer_approved","Record offer approval"),
         _role("offer_sent","Record offer sent"),
         _role("acceptance_recorded","Record offer acceptance"),
         _role("closed","Close recruitment workflow",False),
         _role("rescheduled","Record rescheduling",False)],
        [_parameter("screening_hours","Screening within (elapsed hours)",120),
         _parameter("offer_hours","Offer recording after approval within (elapsed hours)",48),
         _parameter("closure_hours","Closure after acceptance within (elapsed hours)",72)],
        [_time(c,"rec_intake_to_screen","received","screened"),
         _time(c,"rec_screen_to_interview","screened","interviewed"),
         _time(c,"rec_interview_to_approval","interviewed","offer_approved"),
         _time(c,"rec_approval_to_offer","offer_approved","offer_sent"),
         _time(c,"rec_offer_to_acceptance","offer_sent","acceptance_recorded"),
         _time(c,"rec_intake_to_acceptance","received","acceptance_recorded"),
         _count(c,"rec_interview_records","interviewed"),
         _count(c,"rec_reschedule_records","rescheduled")],
        [_deadline(c,"received","screened","screening_hours"),
         _deadline(c,"offer_approved","offer_sent","offer_hours"),
         _deadline(c,"acceptance_recorded","closed","closure_hours"),
         _rule(c,"precedence","received","screened"),
         _rule(c,"precedence","screened","interviewed"),
         _rule(c,"precedence","interviewed","offer_approved"),
         _rule(c,"precedence","offer_approved","offer_sent"),
         _rule(c,"precedence","offer_sent","acceptance_recorded"),
         _rule(c,"precedence","acceptance_recorded","closed"),
         _rule(c,"existence","acceptance_recorded",min=0,max=1)],
        ["This is an accepted-offer administrative path, not an applicant funnel or a hiring decision model. Withdrawals, rejection, parallel interviews and offer renegotiation can legitimately differ.",
         "Do not use these metrics to rank candidates, evaluate employees, infer protected traits or make hiring decisions.",
         "Use pseudonymous case identifiers and administrative event timestamps only. No applications, interview notes, candidate attributes or decision reasons are required.",
         "Recorded acceptance is not proof of contract formation or employment commencement."])


BUSINESS_SUPPORT_PACKS={p["pack_id"]:{p["version"]:p}
                        for p in (_collections(),_access(),_recruitment())}
