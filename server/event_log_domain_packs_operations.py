"""Versioned operational process packs with explicit recorded-evidence limits.

Definitions are static and deterministic. They reuse the normal role mapping,
metric library, compliance engine, object-model replay and install history.
No source connector, live operation or decision authority is introduced.
"""
from __future__ import annotations

from typing import Any

FORMAT = "openworkgraph.domain-pack.v1"


def _role(role, label, required=True):
    return {"role":role,"label":label,"required":required}


def _parameter(name,label,default,unit="hours",maximum=8760):
    return {"name":name,"label":label,"unit":unit,"default":default,
            "min":.25 if unit=="hours" else 1,"max":maximum}


def _time(case,name,start,end):
    return {"case_type":"@"+case,"metric":{"name":name,"kind":"time_between",
             "from_activity":"@"+start,"to_activity":"@"+end}}


def _count(case,name,activity):
    return {"case_type":"@"+case,"metric":{"name":name,"kind":"activity_count","activity":"@"+activity}}


def _rule(case,template,a,b=None,**extra):
    return {"case_type":"@"+case,"rule":{"template":template,"a":"@"+a,
             **({"b":"@"+b} if b is not None else {}),**extra}}


def _deadline(case,a,b,param):
    return _rule(case,"response_within",a,b,seconds="$"+param)


def _sequence(case,roles):
    """One case lifecycle, with optional evidence stages; a reference, not policy."""
    return {"types":[{"object_type":"@"+case,"steps":[
        "@"+r["role"] if r["required"] else {"optional":"@"+r["role"]} for r in roles]}],
        "arcs":[{"activity":"@"+r["role"],"object_type":"@"+case,"variable":False} for r in roles]}


def _pack(pack_id,title,description,case,case_label,roles,parameters,metrics,rules,assumptions):
    return {"format":FORMAT,"pack_id":pack_id,"version":"1.0.0","title":title,
            "description":description,"reference_goal":title+" recorded milestones",
            "object_types":[_role(case,case_label)],"activities":roles,
            "parameters":parameters,"metrics":metrics,"compliance":rules,
            "object_rules":[],"reference_model":_sequence(case,roles),
            "assumptions":assumptions+[
                "One case identifier represents one selected lifecycle in the supplied source. "
                "The mapping cannot verify identity or source completeness.",
                "Targets use elapsed wall-clock time, not business calendars or adopted policy. "
                "Set them explicitly before interpreting results.",
                "Missing or late events can reflect unfinished cases, a partial export or ingestion latency. "
                "Reference-model deviations are observations for review, not confirmed failures.",
                "Repeated records can reflect retries or legitimate repeated work. "
                "No action, approval, staff assessment or policy compliance is authorized or verified."
            ]}


def _contracts():
    case="contract_case"
    roles=[
        _role("request","Open contract request"),
        _role("draft","Record draft prepared"),
        _role("legal_review","Record legal review",False),
        _role("commercial_approval","Record commercial approval",False),
        _role("signature","Record contract signature"),
        _role("repository","Record repository filing",False),
        _role("close","Close contract workflow"),
    ]
    return _pack("contract_lifecycle","Contract lifecycle management",
        "Review elapsed request-to-signature and signature-to-close times, recorded approvals, "
        "filing and duplicate signature records in a selected contract workflow.",case,"Contract workflow",roles,
        [_parameter("draft_days","Record draft within (elapsed days)",5,"days",365),
         _parameter("signature_days","Record signature within (elapsed days)",30,"days",365),
         _parameter("closure_days","Record closure within (elapsed days of signature)",5,"days",365)],
        [_time(case,"contract_request_to_draft","request","draft"),
         _time(case,"contract_request_to_signature","request","signature"),
         _time(case,"contract_signature_to_close","signature","close"),
         _time(case,"contract_draft_to_legal","draft","legal_review"),
         _count(case,"contract_signature_records","signature"),
         _count(case,"contract_approval_records","commercial_approval")],
        [_deadline(case,"request","draft","draft_days"),
         _deadline(case,"request","signature","signature_days"),
         _deadline(case,"signature","close","closure_days"),
         _rule(case,"precedence","request","draft"),
         _rule(case,"precedence","draft","signature"),
         _rule(case,"precedence","signature","close"),
         _rule(case,"precedence","legal_review","signature"),
         _rule(case,"precedence","commercial_approval","signature"),
         _rule(case,"precedence","signature","repository"),
         _rule(case,"precedence","repository","close"),
         _rule(case,"existence","signature",min=1,max=1),
         _rule(case,"segregation_of_duties","draft","commercial_approval")],
        ["Recorded signature is an activity label, not cryptographic signature validation, legal enforceability, "
         "party identity or authority to sign.",
         "The reference is a simple executed-contract path. Negotiation, rejection, amendments, "
         "renewals, multiple signatories and parallel review can legitimately deviate.",
         "An optional review or filing milestone is evaluated only if its role is mapped. "
         "A recorded legal review does not establish legal advice or approval quality.",
         "Separation of duties compares the recorded drafter and commercial approver resources. "
         "Missing resources remain unknown; this is not proof of independent authority."])


def _security():
    case="incident_case"
    roles=[
        _role("detected","Record incident detection"),
        _role("triaged","Record triage"),
        _role("contained","Record containment"),
        _role("investigated","Record investigation",False),
        _role("recovered","Record recovery"),
        _role("reviewed","Record post incident review",False),
        _role("closed","Close incident"),
    ]
    return _pack("security_incident_response","Security incident response operations",
        "Review elapsed detection-to-triage, containment, recovery and closure milestones "
        "in a selected incident-response log; identify observed sequence and timing deviations.",
        case,"Security incident",roles,
        [_parameter("triage_hours","Record triage within (elapsed hours)",4),
         _parameter("containment_hours","Record containment within (elapsed hours)",24),
         _parameter("recovery_hours","Record recovery within (elapsed hours)",72),
         _parameter("review_days","Record review within (elapsed days of recovery)",14,"days",365)],
        [_time(case,"sec_detection_to_triage","detected","triaged"),
         _time(case,"sec_detection_to_containment","detected","contained"),
         _time(case,"sec_containment_to_recovery","contained","recovered"),
         _time(case,"sec_recovery_to_closure","recovered","closed"),
         _time(case,"sec_recovery_to_review","recovered","reviewed"),
         _count(case,"sec_containment_records","contained")],
        [_deadline(case,"detected","triaged","triage_hours"),
         _deadline(case,"detected","contained","containment_hours"),
         _deadline(case,"detected","recovered","recovery_hours"),
         _deadline(case,"recovered","reviewed","review_days"),
         _rule(case,"precedence","detected","triaged"),
         _rule(case,"precedence","triaged","contained"),
         _rule(case,"precedence","contained","recovered"),
         _rule(case,"precedence","recovered","closed"),
         _rule(case,"precedence","recovered","reviewed"),
         _rule(case,"precedence","reviewed","closed"),
         _rule(case,"existence","closed",min=1,max=1),
         _rule(case,"segregation_of_duties","recovered","reviewed")],
        ["The incident scope, severity, detection accuracy, containment effectiveness and recovery integrity "
         "are not verified. No vulnerability scanning or incident response action occurs.",
         "Emergency containment before formal triage, repeated containment, false positives, "
         "parallel investigations and deferred postmortems can be legitimate deviations.",
         "The observed recovery label does not prove eradication, safe operation, notice delivery "
         "or legal reporting compliance.",
         "Optional post-incident review and recorded separation of duties are descriptive checks, "
         "not a determination that personnel should be sanctioned."])


def _maintenance():
    case="maintenance_case"
    roles=[
        _role("requested","Open work order"),
        _role("assigned","Record assignment"),
        _role("authorized","Record work authorization",False),
        _role("started","Record work start"),
        _role("completed","Record work completion"),
        _role("inspected","Record inspection",False),
        _role("closed","Close work order"),
    ]
    return _pack("maintenance_work_orders","Maintenance work order operations",
        "Review assignment, start, completion and closure timing for selected maintenance work orders, "
        "including optional authorization and inspection records.",
        case,"Maintenance work order",roles,
        [_parameter("assignment_hours","Record assignment within (elapsed hours)",8),
         _parameter("start_hours","Record work start within (elapsed hours)",24),
         _parameter("completion_hours","Record completion within (elapsed hours of start)",72),
         _parameter("inspection_hours","Record inspection within (elapsed hours of completion)",24)],
        [_time(case,"maint_request_to_assignment","requested","assigned"),
         _time(case,"maint_request_to_start","requested","started"),
         _time(case,"maint_start_to_completion","started","completed"),
         _time(case,"maint_completion_to_inspection","completed","inspected"),
         _time(case,"maint_completion_to_close","completed","closed"),
         _count(case,"maint_completion_records","completed")],
        [_deadline(case,"requested","assigned","assignment_hours"),
         _deadline(case,"requested","started","start_hours"),
         _deadline(case,"started","completed","completion_hours"),
         _deadline(case,"completed","inspected","inspection_hours"),
         _rule(case,"precedence","requested","assigned"),
         _rule(case,"precedence","assigned","started"),
         _rule(case,"precedence","authorized","started"),
         _rule(case,"precedence","started","completed"),
         _rule(case,"precedence","completed","closed"),
         _rule(case,"precedence","completed","inspected"),
         _rule(case,"precedence","inspected","closed"),
         _rule(case,"existence","completed",min=1,max=1),
         _rule(case,"segregation_of_duties","completed","inspected")],
        ["The work order is not a verified equipment identity or preventive maintenance schedule. "
         "Equipment safety, inspection quality, permits and lockout procedures are not verified.",
         "Do not use a recorded completion or inspection as permission to energize equipment, "
         "return an asset to service, or waive a required physical safety check.",
         "Parts delays, scheduled shutdowns, repeated work, emergency callouts and multi-asset orders "
         "can require different reference models and elapsed targets.",
         "Optional work authorization and inspection are assessed only from mapped activity records. "
         "Missing resource information makes separation-of-duties evidence unknown."])


CONTRACT_LIFECYCLE_1_0_0: dict[str,Any] = _contracts()
SECURITY_INCIDENT_RESPONSE_1_0_0: dict[str,Any] = _security()
MAINTENANCE_WORK_ORDERS_1_0_0: dict[str,Any] = _maintenance()

OPERATIONS_PACKS = {
    p["pack_id"]: {p["version"]:p} for p in
    (CONTRACT_LIFECYCLE_1_0_0, SECURITY_INCIDENT_RESPONSE_1_0_0, MAINTENANCE_WORK_ORDERS_1_0_0)
}
