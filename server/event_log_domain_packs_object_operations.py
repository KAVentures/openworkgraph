"""R23: independently mapped multi-object operations reference packs.

All relationships are supplied event links, never inferred from names or dates.
Each pack is restricted to a selected ordinary completed reference path.
"""
from __future__ import annotations

from .event_log_domain_packs_operations import (
    FORMAT, _role, _parameter, _time, _count, _rule, _deadline,
)


def _bundle(key, title, description, prefix, parent, child, labels, deadlines, assumptions):
    """One parent and several children, with a recorded start/check/close path."""
    p, p_label = parent
    c, c_label = child
    opened, checked, exception, closed = labels
    first_target, total_target = deadlines
    return {
        "format": FORMAT, "pack_id": key, "version": "1.0.0", "title": title,
        "description": description,
        "reference_goal": title + " recorded lifecycle",
        "object_types": [_role(p, p_label), _role(c, c_label)],
        "activities": [_role("opened", opened), _role("checked", checked),
                       _role("exception", exception, False), _role("closed", closed)],
        "parameters": [
            _parameter("check_hours", "Child check within (elapsed hours of opening)", first_target),
            _parameter("close_hours", "Parent closure within (elapsed hours of opening)", total_target),
        ],
        "metrics": [
            _time(p, prefix + "_open_to_close", "opened", "closed"),
            _time(c, prefix + "_open_to_check", "opened", "checked"),
            _time(c, prefix + "_check_to_close", "checked", "closed"),
            _count(c, prefix + "_check_records", "checked"),
            _count(c, prefix + "_exception_records", "exception"),
        ],
        "compliance": [
            _deadline(p, "opened", "closed", "close_hours"),
            _rule(p, "precedence", "opened", "closed"),
            _rule(p, "existence", "opened", min=1, max=1),
            _rule(p, "existence", "closed", min=1, max=1),
            _deadline(c, "opened", "checked", "check_hours"),
            _rule(c, "precedence", "opened", "checked"),
            _rule(c, "precedence", "checked", "closed"),
            _rule(c, "precedence", "opened", "exception"),
            _rule(c, "existence", "checked", min=1),
            _rule(c, "existence", "opened", min=1, max=1),
            _rule(c, "existence", "closed", min=1, max=1),
        ],
        "object_rules": [
            {"template": "event_cardinality", "activity": "@opened", "object_type": "@" + p,
             "min": 1, "max": 1},
            {"template": "event_cardinality", "activity": "@opened", "object_type": "@" + c, "min": 1},
            {"template": "event_cardinality", "activity": "@checked", "object_type": "@" + c,
             "min": 1, "max": 1},
            {"template": "event_cardinality", "activity": "@closed", "object_type": "@" + p,
             "min": 1, "max": 1},
            {"template": "event_cardinality", "activity": "@closed", "object_type": "@" + c, "min": 1},
            {"template": "object_cardinality", "object_type": "@" + c, "related_type": "@" + p,
             "via_activity": "@opened", "min": 1, "max": 1},
            {"template": "object_cardinality", "object_type": "@" + p, "related_type": "@" + c,
             "via_activity": "@opened", "min": 1},
            {"template": "object_cardinality", "object_type": "@" + c, "related_type": "@" + p,
             "via_activity": "@closed", "min": 1, "max": 1},
            {"template": "object_cardinality", "object_type": "@" + c, "related_type": "@" + p,
             "min": 1, "max": 1},
            {"template": "synchronization", "activity": "@closed", "object_type": "@" + p,
             "related_type": "@" + c, "required_activity": "@checked"},
        ],
        "reference_model": {
            "types": [
                {"object_type": "@" + p, "steps": ["@opened", "@closed"]},
                {"object_type": "@" + c,
                 "steps": ["@opened", "@checked", {"optional": "@exception"}, "@closed"]},
            ],
            "arcs": [
                {"activity": "@opened", "object_type": "@" + p, "variable": False},
                {"activity": "@opened", "object_type": "@" + c, "variable": True},
                {"activity": "@checked", "object_type": "@" + c, "variable": False},
                {"activity": "@exception", "object_type": "@" + c, "variable": False},
                {"activity": "@closed", "object_type": "@" + p, "variable": False},
                {"activity": "@closed", "object_type": "@" + c, "variable": True},
            ],
        },
        "assumptions": [
            "Select one ordinary lifecycle per parent record. Opening and closure events link the parent and its children; individual check/exception events link one child. Partial closures, returns and reassignment can legitimately deviate.",
            "Synchronization uses distinct children that share a retained event with the parent, including later links; it is not a time-versioned membership assertion. A check must precede closure in recorded time/event-ID order.",
            "Check targets and closure targets use elapsed wall-clock hours, not working calendars. Set them to the intended review thresholds before interpreting results.",
            "Missing or late events may reflect incomplete data or unfinished work. Counts use the selected parent or child population separately; never sum those populations as unique business cases.",
            "The reference model allows one optional post-check exception record; repeated checks or exceptions may be legitimate deviations. No actual completion, approval, safety or source-system correctness is verified.",
            *assumptions,
        ],
    }


INBOUND_RECEIVING = _bundle(
    "inbound_shipment_receiving", "Inbound shipment receiving",
    "Review inbound shipment closure and handling-unit checks without flattening many units into shipment events.",
    "inbound", ("shipment", "Inbound shipment"), ("handling_unit", "Handling unit"),
    ("Record shipment arrival", "Record handling unit check", "Record receiving discrepancy", "Record receiving closure"),
    (8, 24),
    ["A recorded receiving check is not proof of quantity, condition, ownership or inventory accuracy.",
     "Select shipments closed together; split receipts or units transferred between shipments need a separately reviewed mapping."],
)

PRODUCTION_SAMPLE = _bundle(
    "production_batch_sample_review", "Production batch sample review",
    "Review recorded production batch and sample-check milestones for selected batches with one final review record.",
    "batch_sample", ("batch", "Production batch"), ("sample", "Sample record"),
    ("Record sample review opening", "Record sample check", "Record sample exception", "Record batch review closure"),
    (24, 72),
    ["A sample check label records a milestone, not a passing result, product quality, sampling adequacy or release authority.",
     "No test values, safety thresholds or regulated release decisions are evaluated. Do not treat this report as permission to release a batch."],
)

FIELD_SERVICE = _bundle(
    "field_service_job_tasks", "Field service job and task completion",
    "Review the recorded job lifecycle and task-level checks across a selected ordinary field-service job.",
    "field_job", ("job", "Field service job"), ("task", "Job task"),
    ("Record job opening", "Record task check", "Record task exception", "Record job closure"),
    (48, 96),
    ["Use jobs with a declared task set and a single closure. Rescheduling, cancelled jobs and newly discovered tasks can legitimately deviate.",
     "Recorded checks do not prove physical completion, equipment safety, technician identity or customer acceptance."],
)

OBJECT_OPERATIONS_PACKS = {
    p["pack_id"]: {p["version"]: p}
    for p in (INBOUND_RECEIVING, PRODUCTION_SAMPLE, FIELD_SERVICE)
}
