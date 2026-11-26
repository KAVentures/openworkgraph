"""Conservative projections of minimized operational source receipts."""
from datetime import datetime, timezone


def _ms(value):
    if not isinstance(value,str) or len(value)>64:return None
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        if dt.tzinfo is None:return None
        delta=dt.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
        return delta.days*86400000+delta.seconds*1000+delta.microseconds//1000
    except (ValueError,OverflowError):
        return None


def project(receipt,start,end,now):
    """No paid transition in the window is not proof of failure."""
    result={"provider":"stripe","predicate":"invoice_paid_in_window",
            "status":"unknown","reason":"source_unavailable","paid_at_ms":None,
            "source_read_at_ms":None}
    if not isinstance(receipt,dict) or receipt.get("provider")!="stripe" or receipt.get("availability")!="source_reported":
        return result
    at=_ms(receipt.get("verified_at"))
    if at is None or not end<=at<=now:
        return {**result,"reason":"invalid_source_read_time"}
    result["source_read_at_ms"]=at
    if receipt.get("verification_method")!="opted_in_restricted_key_stripe_account_and_record_get" or receipt.get("source_state_reported") is not True:
        return {**result,"reason":"unrecognized_source_provenance"}
    diag=receipt.get("lifecycle_projection_diagnostics")
    if (receipt.get("object_type")!="invoice" or not isinstance(diag,dict)
            or diag.get("chronology_conflict") is not False
            or type(diag.get("invalid_timestamp_field_count")) is not int
            or diag["invalid_timestamp_field_count"]!=0):
        return {**result,"reason":"incomplete_or_conflicting_lifecycle"}
    events=receipt.get("process_audit_events")
    if not isinstance(events,list) or len(events)>5 or any(not isinstance(x,dict) for x in events):
        return {**result,"reason":"invalid_lifecycle"}
    paid=[_ms(x.get("observed_at")) for x in events if x.get("action")=="invoice_paid"]
    if receipt.get("source_state")!="paid" or receipt.get("verified_business_outcome") is not True:
        return {**result,"reason":"paid_state_not_established"}
    if len(paid)!=1 or paid[0] is None or paid[0]>at:
        return {**result,"reason":"paid_transition_not_established"}
    result["paid_at_ms"]=paid[0]
    if not start<=paid[0] or paid[0]+999>=end:
        return {**result,"reason":"paid_transition_outside_window"}
    return {**result,"status":"confirmed","reason":"paid_transition_in_window"}


def project_jira(receipt,start,end,now):
    """Confirm only Jira's own current resolution with a timestamp in scope.

    Conservative whole-second boundary handling also works for timestamps
    normalized from a source with finer precision. No customer outcome or
    historical continuous resolved-state assertion is made.
    """
    result={"provider":"jira","predicate":"jira_issue_resolved_in_window",
            "status":"unknown","reason":"source_unavailable","resolved_at_ms":None,
            "source_read_at_ms":None}
    if not isinstance(receipt,dict) or receipt.get("provider")!="jira" or receipt.get("availability")!="source_reported":
        return result
    at=_ms(receipt.get("verified_at"))
    if at is None or not end<=at<=now:
        return {**result,"reason":"invalid_source_read_time"}
    result["source_read_at_ms"]=at
    if (receipt.get("verification_method")!="explicit_opt_in_site_verified_oauth_jira_issue_status_read"
            or receipt.get("source_state_reported") is not True or receipt.get("object_type")!="issue"):
        return {**result,"reason":"unrecognized_source_provenance"}
    outcome=receipt.get("verified_operational_outcome")
    if (receipt.get("source_state")!="done" or not isinstance(outcome,dict)
            or outcome.get("kind")!="jira_issue_resolved" or outcome.get("status")!="resolved"
            or outcome.get("verified") is not True
            or outcome.get("scope")!="jira_issue_only_not_customer_acknowledgement_or_refund"):
        return {**result,"reason":"resolved_state_not_established"}
    resolved=_ms(outcome.get("source_timestamp"))
    if resolved is None or resolved>at:
        return {**result,"reason":"resolution_timestamp_not_established"}
    result["resolved_at_ms"]=resolved
    second=resolved//1000*1000
    if second<start or second+999>=end:
        return {**result,"reason":"resolution_timestamp_outside_window"}
    return {**result,"status":"confirmed","reason":"resolution_timestamp_in_window"}


def summarize(state):
    bindings=state.get("source_outcome_bindings",[])
    results=state.get("source_outcome_checks",[])
    observations={o["slot_id"]:o for w in state["snapshot"]["windows"] for o in w["observations"]}
    latest={x["slot_id"]:x for x in results}
    reviewed={row["slot_id"]:row for batch in state.get("outcome_corroborations",[]) for row in batch["rows"]}
    confirmed=0;unknown=0;conflicts=[]
    for b in bindings:
        sid=b["slot_id"];check=latest.get(sid);obs=observations.get(sid)
        if not check or check["status"]!="confirmed":
            unknown+=1;continue
        confirmed+=1
        if obs and (obs["status"]=="failed" or obs["status"]=="completed" and obs["business_outcome"]=="failure"):
            conflicts.append({"window_id":b["window_id"],"slot_id":sid,"reason":"source_predicate_conflicts_with_declared_failure"})
        if sid in reviewed and reviewed[sid]["business_outcome"]=="failure":
            conflicts.append({"window_id":b["window_id"],"slot_id":sid,"reason":"source_predicate_conflicts_with_separate_reviewer_failure"})
    return {"bound":len(bindings),"confirmed":confirmed,"unknown":unknown,
            "conflicts":conflicts,"blocks_continuation":bool(conflicts),
            "case_to_source_link_independently_verified":False,
            "causal_business_success_verified":False}
