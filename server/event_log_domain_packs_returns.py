"""Versioned returns/refunds domain pack for explicitly mapped event-log evidence.

This is a descriptive reference, not a policy, payment receipt, entitlement
decision, source completeness claim or authorization to issue a refund.
"""
from __future__ import annotations

RETURNS_REFUNDS_1_0_0 = {
    "format": "openworkgraph.domain-pack.v1",
    "pack_id": "returns_refunds",
    "version": "1.0.0",
    "title": "Returns and refunds",
    "reference_goal": "Review a return request through assessment and optional refund",
    "description": (
        "Review return-request assessment time, approved-return refund delays, "
        "rejections, repeated refund events and out-of-order recorded steps. "
        "An approved return with no recorded refund is missing evidence in this "
        "log, not proof that a customer is owed money."
    ),
    "object_types": [
        {"role": "return_case", "label": "Return case", "required": True},
        {"role": "refund", "label": "Refund record", "required": False},
        {"role": "item", "label": "Returned item", "required": False},
    ],
    "activities": [
        {"role": "request", "label": "Request return", "required": True},
        {"role": "receive", "label": "Receive return", "required": False},
        {"role": "inspect", "label": "Inspect return", "required": True},
        {"role": "approve", "label": "Approve return", "required": False},
        {"role": "reject", "label": "Reject return", "required": False},
        {"role": "refund", "label": "Record refund", "required": False},
        {"role": "close", "label": "Close return", "required": False},
    ],
    "parameters": [
        {"name": "assessment_days", "label": "Assess within (elapsed days of request)",
         "unit": "days", "default": 7, "min": 1, "max": 365},
        {"name": "refund_days", "label": "Record refund within (elapsed days of approval)",
         "unit": "days", "default": 14, "min": 1, "max": 365},
    ],
    "metrics": [
        {"case_type": "@return_case", "metric": {
            "name": "returns_request_to_assessment", "kind": "time_between",
            "from_activity": "@request", "to_activity": "@inspect"}},
        {"case_type": "@return_case", "metric": {
            "name": "returns_approval_to_refund", "kind": "time_between",
            "from_activity": "@approve", "to_activity": "@refund"}},
        {"case_type": "@return_case", "metric": {
            "name": "returns_request_to_refund", "kind": "time_between",
            "from_activity": "@request", "to_activity": "@refund"}},
        {"case_type": "@return_case", "metric": {
            "name": "returns_refund_records", "kind": "activity_count", "activity": "@refund"}},
        {"case_type": "@return_case", "metric": {
            "name": "returns_rejections", "kind": "activity_count", "activity": "@reject"}},
    ],
    "compliance": [
        {"case_type": "@return_case", "rule": {
            "template": "response_within", "a": "@request", "b": "@inspect",
            "seconds": "$assessment_days"}},
        {"case_type": "@return_case", "rule": {
            "template": "response_within", "a": "@approve", "b": "@refund",
            "seconds": "$refund_days"}},
        {"case_type": "@return_case", "rule": {
            "template": "precedence", "a": "@request", "b": "@inspect"}},
        {"case_type": "@return_case", "rule": {
            "template": "precedence", "a": "@inspect", "b": "@approve"}},
        {"case_type": "@return_case", "rule": {
            "template": "precedence", "a": "@inspect", "b": "@reject"}},
        {"case_type": "@return_case", "rule": {
            "template": "precedence", "a": "@approve", "b": "@refund"}},
        {"case_type": "@return_case", "rule": {
            "template": "precedence", "a": "@refund", "b": "@close"}},
        {"case_type": "@return_case", "rule": {
            "template": "existence", "a": "@refund", "max": 1}},
    ],
    "object_rules": [
        {"template": "event_cardinality", "activity": "@refund",
         "object_type": "@return_case", "min": 1, "max": 1},
        {"template": "object_cardinality", "object_type": "@return_case",
         "related_type": "@item", "min": 1},
    ],
    "reference_model": {
        "types": [
            {"object_type": "@return_case", "steps": [
                "@request", {"optional": "@receive"}, "@inspect",
                {"optional": "@approve"}, {"optional": "@refund"},
                {"optional": "@close"}]},
            {"object_type": "@refund", "steps": ["@refund"]},
        ],
        "arcs": [
            {"activity": "@request", "object_type": "@return_case", "variable": False},
            {"activity": "@receive", "object_type": "@return_case", "variable": False},
            {"activity": "@inspect", "object_type": "@return_case", "variable": False},
            {"activity": "@approve", "object_type": "@return_case", "variable": False},
            {"activity": "@refund", "object_type": "@return_case", "variable": False},
            {"activity": "@close", "object_type": "@return_case", "variable": False},
            {"activity": "@refund", "object_type": "@refund", "variable": False},
        ],
    },
    "assumptions": [
        "Each supplied return case ID identifies one reviewed request in one source and tenant. "
        "Do not combine identically named IDs from different systems or accounts.",
        "Assessment and refund targets are elapsed days, not business days or an adopted "
        "merchant policy; they must be reviewed and configured for each use.",
        "Only an explicitly recorded approval activates the approval-to-refund check. "
        "A rejection without approval is not treated as a missed refund obligation.",
        "The reference lifecycle describes a common approved-return path, not a required "
        "process. A legitimate rejection is an alternative and may be reported as an "
        "unmodelled activity. No rejection/approval policy is inferred.",
        "A posted refund event does not prove bank settlement, amount, currency, customer "
        "entitlement or the absence of a reversal. No payment provider is queried.",
        "More than one refund event is flagged for review, not declared a duplicate "
        "payment: partial refunds, separate items, reversals and retries may be valid.",
        "Missing events, censored open cases, delayed feeds and omitted source records "
        "prevent conclusions about debt, overdue obligations or business outcomes.",
        "Object relationships come only from supplied source mappings; a return linked "
        "to an item does not establish that the item was physically received.",
        "No report, metric or reference lifecycle authorizes a refund or any source write.",
    ],
}
