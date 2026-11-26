"""Illustrative operational lifecycle packs; no source access or action authority."""
from __future__ import annotations

FORMAT = "openworkgraph.domain-pack.v1"


def _pack(key, title, case, label, roles, optional, targets, pairs, counts,
          steps, prerequisites, separation, assumptions):
    ref = "@" + case
    # Stable, distinct prefixes fit the metric library\'s 40-character identifier limit.
    metric_prefix = {"capital_expenditure_request": "capex_request",
                     "loan_application_operations": "loan_application"}.get(key, key)
    def metric(name, kind, **kw):
        return {"case_type": ref, "metric": {"name": metric_prefix + "_" + name, "kind": kind, **kw}}

    def rule(template, **kw):
        return {"case_type": ref, "rule": {"template": template, **kw}}

    return {
        "format": FORMAT, "pack_id": key, "version": "1.0.0", "title": title,
        "description": "Review recorded milestones, elapsed times and deviations in " + title.lower() + ".",
        "object_types": [{"role": case, "label": label, "required": True}],
        "activities": [{"role": role, "label": name, "required": role not in optional}
                       for role, name in roles],
        "parameters": [{"name": name, "label": name_label, "default": default,
                        "min": .25, "max": 8760, "unit": "hours"}
                       for name, name_label, default, _, _ in targets],
        "metrics": [metric(name, "time_between", from_activity="@" + a, to_activity="@" + b)
                    for name, a, b in pairs] +
                   [metric(name, "activity_count", activity="@" + a) for name, a in counts],
        "compliance": [rule("response_within", a="@" + a, b="@" + b, seconds="$" + name)
                       for name, _, _, a, b in targets] +
                      [rule("precedence", a="@" + a, b="@" + b) for a, b in prerequisites] +
                      [rule("segregation_of_duties", a="@" + a, b="@" + b) for a, b in separation],
        "object_rules": [],
        "reference_model": {
            "types": [{"object_type": ref, "steps": [
                {"optional": "@" + role} if role in optional else "@" + role for role in steps]}],
            "arcs": [{"activity": "@" + role, "object_type": ref, "variable": False} for role in steps],
        },
        "assumptions": [
            "Each case is a declared source record, not a verified identity.",
            "Targets are editable elapsed-time assumptions, not adopted policy or legal obligations.",
            "Missing or repeated records may reflect incomplete history or legitimate exceptions.",
            "Recorded resources do not prove identity, independence or authority.",
            "The illustrative reference path grants no execution authority.", *assumptions,
        ],
    }


SUBSCRIPTION_RENEWAL = _pack(
    "subscription_renewal", "Subscription renewal operations", "cycle", "Renewal cycle",
    [("opened", "Open renewal cycle"), ("notified", "Send renewal notice"),
     ("reviewed", "Record account review"), ("renewed", "Record renewal"),
     ("billed", "Record billing"), ("closed", "Close renewal cycle"), ("retried", "Record retry")],
    {"reviewed", "retried"},
    [("notice_hours", "Notice within (hours of opening)", 48, "opened", "notified"),
     ("billing_hours", "Billing within (hours of renewal)", 24, "renewed", "billed")],
    [("notice_time", "opened", "notified"), ("renewal_time", "opened", "renewed"),
     ("billing_time", "renewed", "billed"), ("close_time", "billed", "closed"),
     ("review_time", "opened", "reviewed")],
    [("retries", "retried")],
    ["opened", "notified", "reviewed", "renewed", "billed", "closed"],
    [("opened", "notified"), ("notified", "renewed"), ("renewed", "billed"), ("billed", "closed")],
    [],
    ["Select renewal cycles, not entire customer lifetimes; cancellations and failed renewals can deviate.",
     "Billing labels do not prove payment, consent, notice delivery or contract validity."])

LENDING_OPERATIONS = _pack(
    "loan_application_operations", "Loan application operations", "application", "Application",
    [("received", "Receive application"), ("checked", "Record completeness check"),
     ("assessed", "Record assessment"), ("decided", "Record decision"),
     ("communicated", "Record decision communication"), ("closed", "Close application"),
     ("clarified", "Request clarification")],
    {"clarified"},
    [("check_hours", "Completeness check within (hours)", 48, "received", "checked"),
     ("communication_hours", "Communication within (hours of decision)", 24, "decided", "communicated")],
    [("check_time", "received", "checked"), ("decision_time", "received", "decided"),
     ("communication_time", "decided", "communicated"), ("close_time", "communicated", "closed")],
    [("clarifications", "clarified")],
    ["received", "checked", "assessed", "decided", "communicated", "closed"],
    [("received", "checked"), ("checked", "assessed"), ("assessed", "decided"),
     ("decided", "communicated"), ("communicated", "closed")],
    [("assessed", "decided")],
    ["Decision includes approval or rejection; no applicant attributes or credit scoring are used.",
     "Withdrawal and incomplete cases may deviate. This is not underwriting or adverse-action compliance."])

CAPITAL_REQUEST = _pack(
    "capital_expenditure_request", "Capital expenditure request operations", "request", "Capital request",
    [("submitted", "Submit request"), ("reviewed", "Record budget review"),
     ("decided", "Record authorization decision"), ("closed", "Close request"),
     ("reworked", "Return for rework"), ("audited", "Record independent review")],
    {"reworked", "audited"},
    [("review_hours", "Budget review within (hours)", 72, "submitted", "reviewed"),
     ("decision_hours", "Decision within (hours of review)", 168, "reviewed", "decided")],
    [("review_time", "submitted", "reviewed"), ("decision_time", "submitted", "decided"),
     ("close_time", "decided", "closed"), ("audit_time", "decided", "audited")],
    [("reworks", "reworked")],
    ["submitted", "reviewed", "decided", "audited", "closed"],
    [("submitted", "reviewed"), ("reviewed", "decided"), ("decided", "closed")],
    [("submitted", "decided"), ("decided", "audited")],
    ["Authorization decisions may be negative; the pack does not verify approvals, budgets or spend.",
     "Parallel committees and repeated decisions can legitimately deviate."])

FINANCE_LIFECYCLE_PACKS = {p["pack_id"]: {p["version"]: p}
                           for p in (SUBSCRIPTION_RENEWAL, LENDING_OPERATIONS, CAPITAL_REQUEST)}
