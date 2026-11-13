"""Versioned domain packs for saved event logs, including returns/refunds (#370 R23).

A domain pack bundles definitions a process team would otherwise rebuild for every log of a well-known process:

- **roles:** the object types and activities the process has, by meaning (``order``, ``create_invoice``). They
  are mapped to the names a particular log uses, because no two source systems name them alike;
- **case metrics** for the existing typed analysis, **compliance rules** per object type, **object rules**
  across types and an **object-centric reference model**, all written against roles;
- **parameters** with stated defaults (payment terms, invoicing target) that a team changes to its own policy.

Installing a pack on a log records the pack version, the mapping and the parameters, pins a digest of the
resolved definitions, and saves the pack's case metrics to the log's metric library, owned by the install.
Installing a newer version upgrades it and records the previous one; a downgrade is refused. A report runs every
resolved definition against the log in one call. Definitions that use an optional role left unmapped are listed
as not applicable rather than silently dropped.

Each pack states assumptions about a typical process; it is not validated against any particular ERP. A mapping
is the user's assertion that a log's names mean the pack's roles. Results describe the log, not verified business
outcomes, policy compliance or the source system's completeness.
"""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_compliance as compliance
from . import event_log_object_constraints as object_constraints
from . import event_log_object_process_model as object_model
from . import event_log_query as query
from . import event_log_workspace as workspace
from .event_log_domain_packs_returns import RETURNS_REFUNDS_1_0_0

FORMAT = "openworkgraph.domain-pack.v1"
INSTALL_FORMAT = "openworkgraph.domain-pack-install.v1"
MAX_HISTORY = 20
MAX_NAMES = 1000          # object types and activities offered for mapping
Name = Annotated[str, Field(min_length=1, max_length=200)]

_O2C_ROLES_TYPES = [
    {"role": "order", "label": "Sales order", "required": True},
    {"role": "item", "label": "Order item", "required": True},
    {"role": "delivery", "label": "Delivery", "required": True},
    {"role": "invoice", "label": "Invoice", "required": True},
    {"role": "payment", "label": "Payment", "required": False},
]
_O2C_ROLES_ACTIVITIES = [
    {"role": "create_order", "label": "Create order", "required": True},
    {"role": "approve_credit", "label": "Approve credit", "required": False},
    {"role": "change_order", "label": "Change order", "required": False},
    {"role": "pick_item", "label": "Pick item", "required": True},
    {"role": "ship", "label": "Ship", "required": True},
    {"role": "create_invoice", "label": "Create invoice", "required": True},
    {"role": "receive_payment", "label": "Receive payment", "required": True},
]

ORDER_TO_CASH_1_0_0: dict[str, Any] = {
    "format": FORMAT, "pack_id": "order_to_cash", "version": "1.0.0", "title": "Order to cash",
    "reference_goal": "Order-to-cash reference lifecycle",
    "description": ("Sales orders with items, deliveries, invoices and payments: the time from order to invoice "
                    "and from invoice to payment, rework through order changes, credit approval by someone other "
                    "than the order's creator, picking before shipping and the object relations around shipping "
                    "and invoicing."),
    "object_types": _O2C_ROLES_TYPES,
    "activities": _O2C_ROLES_ACTIVITIES,
    "parameters": [
        {"name": "invoice_within_days", "label": "Invoice within (days of order)", "default": 14, "min": 1, "max": 365},
        {"name": "payment_terms_days", "label": "Payment terms (days of invoice)", "default": 30, "min": 1, "max": 365},
    ],
    "metrics": [
        {"case_type": "@order", "metric": {"name": "o2c_order_to_invoice", "kind": "time_between",
                                           "from_activity": "@create_order", "to_activity": "@create_invoice"}},
        {"case_type": "@order", "metric": {"name": "o2c_order_changes", "kind": "activity_count",
                                           "activity": "@change_order"}},
        {"case_type": "@invoice", "metric": {"name": "o2c_invoice_to_payment", "kind": "time_between",
                                             "from_activity": "@create_invoice", "to_activity": "@receive_payment"}},
        {"case_type": "@item", "metric": {"name": "o2c_item_pick_to_ship", "kind": "time_between",
                                          "from_activity": "@pick_item", "to_activity": "@ship"}},
    ],
    "compliance": [
        {"case_type": "@order", "rule": {"template": "precedence", "a": "@create_order", "b": "@create_invoice"}},
        {"case_type": "@order", "rule": {"template": "response_within", "a": "@create_order", "b": "@create_invoice",
                                         "seconds": "$invoice_within_days"}},
        {"case_type": "@order", "rule": {"template": "segregation_of_duties", "a": "@create_order",
                                         "b": "@approve_credit"}},
        {"case_type": "@item", "rule": {"template": "precedence", "a": "@pick_item", "b": "@ship"}},
        {"case_type": "@invoice", "rule": {"template": "response_within", "a": "@create_invoice",
                                           "b": "@receive_payment", "seconds": "$payment_terms_days"}},
        {"case_type": "@invoice", "rule": {"template": "existence", "a": "@receive_payment", "min": 1}},
    ],
    "object_rules": [
        {"template": "event_cardinality", "activity": "@ship", "object_type": "@delivery", "min": 1, "max": 1},
        {"template": "synchronization", "activity": "@ship", "object_type": "@delivery", "related_type": "@item",
         "required_activity": "@pick_item"},
        {"template": "object_cardinality", "object_type": "@order", "related_type": "@item",
         "via_activity": "@create_order", "min": 1},
        {"template": "event_cardinality", "activity": "@create_invoice", "object_type": "@order", "min": 1, "max": 1},
    ],
    # Lifecycles per type: a sequence of activities, where {"optional": ...} may be skipped.
    "reference_model": {
        "types": [
            {"object_type": "@order", "steps": ["@create_order", {"optional": "@approve_credit"},
                                                {"optional": "@change_order"}, "@create_invoice"]},
            {"object_type": "@item", "steps": ["@create_order", "@pick_item", "@ship"]},
            {"object_type": "@delivery", "steps": ["@ship"]},
            {"object_type": "@invoice", "steps": ["@create_invoice", "@receive_payment"]},
            {"object_type": "@payment", "steps": ["@receive_payment"]},
        ],
        "arcs": [
            {"activity": "@create_order", "object_type": "@order", "variable": False},
            {"activity": "@create_order", "object_type": "@item", "variable": True},
            {"activity": "@approve_credit", "object_type": "@order", "variable": False},
            {"activity": "@change_order", "object_type": "@order", "variable": False},
            {"activity": "@pick_item", "object_type": "@item", "variable": False},
            {"activity": "@ship", "object_type": "@delivery", "variable": False},
            {"activity": "@ship", "object_type": "@item", "variable": True},
            {"activity": "@create_invoice", "object_type": "@invoice", "variable": False},
            {"activity": "@create_invoice", "object_type": "@order", "variable": False},
            {"activity": "@receive_payment", "object_type": "@invoice", "variable": True},
            {"activity": "@receive_payment", "object_type": "@payment", "variable": False},
        ],
    },
    "assumptions": [
        "Each order is invoiced once, by one invoice; partial invoicing shows as deviations.",
        "A payment may settle several invoices; each invoice is expected to be paid at least once.",
        "Credit approval and order changes are optional steps; without them mapped, their rules do not apply.",
        "Segregation of duties compares the recorded resources of creating and approving the order; cases "
        "without a recorded resource, which includes every OCEL import, are reported as unknown, never satisfied.",
        "Payment terms and the invoicing target are parameters with stated defaults, not your policy until set.",
    ],
}

PROCURE_TO_PAY_1_0_0: dict[str, Any] = {
    "format": FORMAT, "pack_id": "procure_to_pay", "version": "1.0.0", "title": "Procure to pay",
    "description": ("Purchase requisitions, purchase orders, goods receipts, invoices and payments: three-way match "
                    "(no invoice before the goods are received), no invoice without a purchase order, approval of "
                    "orders by someone other than their buyer, goods received only for approved orders, delivery "
                    "and payment times, and invoices paid exactly once."),
    "object_types": [
        {"role": "requisition", "label": "Purchase requisition", "required": False},
        {"role": "purchase_order", "label": "Purchase order", "required": True},
        {"role": "goods_receipt", "label": "Goods receipt", "required": False},
        {"role": "invoice", "label": "Vendor invoice", "required": True},
        {"role": "payment", "label": "Payment", "required": False},
    ],
    "activities": [
        {"role": "create_requisition", "label": "Create requisition", "required": False},
        {"role": "approve_requisition", "label": "Approve requisition", "required": False},
        {"role": "create_po", "label": "Create purchase order", "required": True},
        {"role": "approve_po", "label": "Approve purchase order", "required": False},
        {"role": "change_po", "label": "Change purchase order", "required": False},
        {"role": "receive_goods", "label": "Receive goods", "required": True},
        {"role": "post_invoice", "label": "Post invoice", "required": True},
        {"role": "pay_invoice", "label": "Pay invoice", "required": True},
    ],
    "parameters": [
        {"name": "delivery_within_days", "label": "Delivery within (days of order)", "default": 14, "min": 1, "max": 365},
        {"name": "payment_terms_days", "label": "Payment terms (days of invoice)", "default": 30, "min": 1, "max": 365},
    ],
    "metrics": [
        {"case_type": "@requisition", "metric": {"name": "p2p_requisition_to_order", "kind": "time_between",
                                                 "from_activity": "@create_requisition", "to_activity": "@create_po"}},
        {"case_type": "@purchase_order", "metric": {"name": "p2p_order_to_receipt", "kind": "time_between",
                                                    "from_activity": "@create_po", "to_activity": "@receive_goods"}},
        {"case_type": "@purchase_order", "metric": {"name": "p2p_order_changes", "kind": "activity_count",
                                                    "activity": "@change_po"}},
        {"case_type": "@invoice", "metric": {"name": "p2p_invoice_to_payment", "kind": "time_between",
                                             "from_activity": "@post_invoice", "to_activity": "@pay_invoice"}},
    ],
    "compliance": [
        {"case_type": "@purchase_order", "rule": {"template": "precedence", "a": "@receive_goods", "b": "@post_invoice"}},
        {"case_type": "@purchase_order", "rule": {"template": "response_within", "a": "@create_po", "b": "@receive_goods",
                                                  "seconds": "$delivery_within_days"}},
        {"case_type": "@purchase_order", "rule": {"template": "segregation_of_duties", "a": "@create_po",
                                                  "b": "@approve_po"}},
        {"case_type": "@purchase_order", "rule": {"template": "precedence", "a": "@approve_po", "b": "@receive_goods"}},
        {"case_type": "@requisition", "rule": {"template": "precedence", "a": "@approve_requisition", "b": "@create_po"}},
        {"case_type": "@invoice", "rule": {"template": "response_within", "a": "@post_invoice", "b": "@pay_invoice",
                                           "seconds": "$payment_terms_days"}},
        {"case_type": "@invoice", "rule": {"template": "existence", "a": "@pay_invoice", "min": 1, "max": 1}},
    ],
    "object_rules": [
        {"template": "event_cardinality", "activity": "@post_invoice", "object_type": "@purchase_order", "min": 1},
        {"template": "event_cardinality", "activity": "@receive_goods", "object_type": "@purchase_order", "min": 1, "max": 1},
        {"template": "synchronization", "activity": "@post_invoice", "object_type": "@invoice",
         "related_type": "@purchase_order", "required_activity": "@receive_goods"},
    ],
    "reference_model": {
        "types": [
            {"object_type": "@requisition", "steps": ["@create_requisition", {"optional": "@approve_requisition"},
                                                      "@create_po"]},
            {"object_type": "@purchase_order", "steps": ["@create_po", {"optional": "@approve_po"},
                                                         {"optional": "@change_po"}, "@receive_goods", "@post_invoice"]},
            {"object_type": "@goods_receipt", "steps": ["@receive_goods"]},
            {"object_type": "@invoice", "steps": ["@post_invoice", "@pay_invoice"]},
            {"object_type": "@payment", "steps": ["@pay_invoice"]},
        ],
        "arcs": [
            {"activity": "@create_requisition", "object_type": "@requisition", "variable": False},
            {"activity": "@approve_requisition", "object_type": "@requisition", "variable": False},
            {"activity": "@create_po", "object_type": "@requisition", "variable": True},
            {"activity": "@create_po", "object_type": "@purchase_order", "variable": False},
            {"activity": "@approve_po", "object_type": "@purchase_order", "variable": False},
            {"activity": "@change_po", "object_type": "@purchase_order", "variable": False},
            {"activity": "@receive_goods", "object_type": "@purchase_order", "variable": False},
            {"activity": "@receive_goods", "object_type": "@goods_receipt", "variable": False},
            {"activity": "@post_invoice", "object_type": "@invoice", "variable": False},
            {"activity": "@post_invoice", "object_type": "@purchase_order", "variable": True},
            {"activity": "@pay_invoice", "object_type": "@invoice", "variable": True},
            {"activity": "@pay_invoice", "object_type": "@payment", "variable": False},
        ],
    },
    "assumptions": [
        "Each purchase order is received once and invoiced once; partial deliveries or invoices show as deviations.",
        "An invoice may cover several purchase orders, and a payment may settle several invoices.",
        "Three-way match is checked as order of events only: amounts and quantities are not compared.",
        "Segregation of duties compares the recorded resources of creating and approving the order; cases "
        "without a recorded resource, which includes every OCEL import, are reported as unknown, never satisfied.",
        "Delivery and payment terms are parameters with stated defaults, not your policy until set.",
    ],
}

SERVICE_MANAGEMENT_1_0_0: dict[str, Any] = {
    "format": FORMAT, "pack_id": "service_management", "version": "1.0.0", "title": "IT service management",
    "description": ("Incidents from logging to resolution: time to assign and to resolve against response and "
                    "resolution targets, reassignments between groups, reopened incidents, and resolution before "
                    "closure. Works on a single-case CSV export as well as on an object-centric log."),
    "object_types": [
        {"role": "incident", "label": "Incident", "required": True},
        {"role": "problem", "label": "Problem", "required": False},
    ],
    "activities": [
        {"role": "log_incident", "label": "Log incident", "required": True},
        {"role": "assign", "label": "Assign", "required": True},
        {"role": "start_work", "label": "Start work", "required": False},
        {"role": "reassign", "label": "Reassign", "required": False},
        {"role": "resolve", "label": "Resolve", "required": True},
        {"role": "reopen", "label": "Reopen", "required": False},
        {"role": "close", "label": "Close", "required": False},
    ],
    "parameters": [
        {"name": "response_hours", "label": "Assign within (hours of logging)", "unit": "hours",
         "default": 4, "min": 0.25, "max": 720},
        {"name": "resolution_hours", "label": "Resolve within (hours of logging)", "unit": "hours",
         "default": 72, "min": 1, "max": 8760},
    ],
    "metrics": [
        {"case_type": "@incident", "metric": {"name": "itsm_time_to_assign", "kind": "time_between",
                                              "from_activity": "@log_incident", "to_activity": "@assign"}},
        {"case_type": "@incident", "metric": {"name": "itsm_time_to_resolve", "kind": "time_between",
                                              "from_activity": "@log_incident", "to_activity": "@resolve",
                                              "to_occurrence": "last"}},
        {"case_type": "@incident", "metric": {"name": "itsm_reassignments", "kind": "activity_count",
                                              "activity": "@reassign"}},
        {"case_type": "@incident", "metric": {"name": "itsm_reopens", "kind": "activity_count", "activity": "@reopen"}},
    ],
    "compliance": [
        {"case_type": "@incident", "rule": {"template": "response_within", "a": "@log_incident", "b": "@assign",
                                            "seconds": "$response_hours"}},
        {"case_type": "@incident", "rule": {"template": "response_within", "a": "@log_incident", "b": "@resolve",
                                            "seconds": "$resolution_hours"}},
        {"case_type": "@incident", "rule": {"template": "precedence", "a": "@assign", "b": "@resolve"}},
        {"case_type": "@incident", "rule": {"template": "existence", "a": "@reassign", "max": 2}},
        {"case_type": "@incident", "rule": {"template": "existence", "a": "@reopen", "max": 0}},
        {"case_type": "@incident", "rule": {"template": "precedence", "a": "@resolve", "b": "@close"}},
    ],
    "object_rules": [
        {"template": "event_cardinality", "activity": "@resolve", "object_type": "@incident", "min": 1, "max": 1},
        {"template": "object_cardinality", "object_type": "@problem", "related_type": "@incident", "min": 1},
    ],
    "reference_model": {
        "types": [
            {"object_type": "@incident", "steps": ["@log_incident", "@assign", {"optional": "@start_work"},
                                                   "@resolve", {"optional": "@close"}]},
        ],
        "arcs": [
            {"activity": "@log_incident", "object_type": "@incident", "variable": False},
            {"activity": "@assign", "object_type": "@incident", "variable": False},
            {"activity": "@start_work", "object_type": "@incident", "variable": False},
            {"activity": "@resolve", "object_type": "@incident", "variable": False},
            {"activity": "@close", "object_type": "@incident", "variable": False},
        ],
    },
    "assumptions": [
        "The reference lifecycle is the happy path: reassignments and reopenings show as deviations from it, by "
        "design, and are also counted by their own rules.",
        "More than two reassignments of one incident counts as ping-pong between groups.",
        "Response and resolution are measured from the first logging to the first assignment and to each "
        "resolution; time with the requester, on hold or outside support hours is not excluded.",
        "Response and resolution targets are parameters with stated defaults, not your service levels until set.",
    ],
}

SALES_LEAD_1_0_0: dict[str, Any] = {
    "format": FORMAT, "pack_id": "sales_lead_management", "version": "1.0.0",
    "title": "Sales lead qualification",
    "reference_goal": "Lead qualification and opportunity handoff reference lifecycle",
    "description": ("Review how recorded sales leads move from creation through qualification to an "
                    "opportunity handoff. Compare elapsed times with your chosen review targets and inspect "
                    "missing or out-of-order steps. The selected log defines the case population."),
    "object_types": [
        {"role": "lead", "label": "Lead case", "required": True},
    ],
    "activities": [
        {"role": "create_lead", "label": "Create lead", "required": True},
        {"role": "qualify_lead", "label": "Qualify lead", "required": True},
        {"role": "create_opportunity", "label": "Create opportunity", "required": True},
        {"role": "review_lead", "label": "Review lead", "required": False},
        {"role": "reopen_lead", "label": "Reopen lead", "required": False},
    ],
    "parameters": [
        {"name": "qualification_hours", "label": "Qualify within (hours of creation)", "unit": "hours",
         "default": 48, "min": 0.25, "max": 8760},
        {"name": "handoff_hours", "label": "Create opportunity within (hours of qualification)", "unit": "hours",
         "default": 24, "min": 0.25, "max": 8760},
    ],
    "metrics": [
        {"case_type": "@lead", "metric": {"name": "sales_lead_time_to_qualify", "kind": "time_between",
                                        "from_activity": "@create_lead", "to_activity": "@qualify_lead"}},
        {"case_type": "@lead", "metric": {"name": "sales_lead_qualification_to_opportunity", "kind": "time_between",
                                        "from_activity": "@qualify_lead", "to_activity": "@create_opportunity"}},
        {"case_type": "@lead", "metric": {"name": "sales_lead_reviews", "kind": "activity_count",
                                        "activity": "@review_lead"}},
        {"case_type": "@lead", "metric": {"name": "sales_lead_reopens", "kind": "activity_count",
                                        "activity": "@reopen_lead"}},
    ],
    "compliance": [
        {"case_type": "@lead", "rule": {"template": "precedence", "a": "@create_lead", "b": "@qualify_lead"}},
        {"case_type": "@lead", "rule": {"template": "precedence", "a": "@qualify_lead", "b": "@create_opportunity"}},
        {"case_type": "@lead", "rule": {"template": "response_within", "a": "@create_lead", "b": "@qualify_lead",
                                      "seconds": "$qualification_hours"}},
        {"case_type": "@lead", "rule": {"template": "response_within", "a": "@qualify_lead", "b": "@create_opportunity",
                                      "seconds": "$handoff_hours"}},
        {"case_type": "@lead", "rule": {"template": "existence", "a": "@create_opportunity", "min": 0, "max": 1}},
    ],
    "object_rules": [],
    "reference_model": {
        "types": [
            {"object_type": "@lead", "steps": ["@create_lead", {"optional": "@review_lead"},
                                             "@qualify_lead", "@create_opportunity"]},
        ],
        "arcs": [
            {"activity": "@create_lead", "object_type": "@lead", "variable": False},
            {"activity": "@review_lead", "object_type": "@lead", "variable": False},
            {"activity": "@qualify_lead", "object_type": "@lead", "variable": False},
            {"activity": "@create_opportunity", "object_type": "@lead", "variable": False},
        ],
    },
    "assumptions": [
        "The reference is a single lead's successful qualification and opportunity handoff, not the full sales pipeline.",
        "Open, rejected, disqualified or unobserved cases may appear as missing responses or model deviations; "
        "these results do not establish a missed obligation or an actual breach.",
        "At most one opportunity-creation event per lead is an illustrative check; repeated events may be valid "
        "or refer to the same opportunity and are flagged for review, not counted as distinct CRM opportunities.",
        "The simple reference path permits at most one review and does not model reopening, so repeated reviews "
        "or a mapped reopen may appear as deviations.",
        "Time metrics compare the first recorded occurrence of each mapped activity, include waiting and "
        "nonworking hours, and are not employee effort. Response rules check each initiating event.",
        "Targets are editable illustrative parameters, not your approved sales policy or service levels.",
        "Review and reopen activities are optional mappings; missing mappings leave their metrics unavailable.",
        "No source identity, revenue, conversion, permission to act or business outcome is verified by this pack.",
    ],
}


CUSTOMER_ONBOARDING_1_0_0: dict[str, Any] = {
    "format": FORMAT, "pack_id": "customer_onboarding", "version": "1.0.0",
    "title": "Customer onboarding and activation",
    "reference_goal": "Customer onboarding to recorded activation reference lifecycle",
    "description": ("Review the recorded handoff from an opened customer onboarding case through access "
                    "provisioning and activation confirmation. Inspect elapsed time, repeated rework and missing "
                    "or out-of-order steps using the selected log's case identities."),
    "object_types": [
        {"role": "onboarding_case", "label": "Onboarding case", "required": True},
    ],
    "activities": [
        {"role": "case_opened", "label": "Open onboarding case", "required": True},
        {"role": "handoff_received", "label": "Receive handoff", "required": False},
        {"role": "requirements_verified", "label": "Verify prerequisites", "required": False},
        {"role": "access_provisioned", "label": "Provision access", "required": True},
        {"role": "activation_confirmed", "label": "Confirm activation", "required": True},
        {"role": "rework_requested", "label": "Request rework", "required": False},
        {"role": "case_closed", "label": "Close case", "required": False},
    ],
    "parameters": [
        {"name": "provision_hours", "label": "Provision within (elapsed hours of opening)", "unit": "hours",
         "default": 48, "min": 0.25, "max": 8760},
        {"name": "activation_days", "label": "Confirm activation within (elapsed days of provisioning)",
         "unit": "days", "default": 14, "min": 1, "max": 365},
    ],
    "metrics": [
        {"case_type": "@onboarding_case",
         "metric": {"name": "onboarding_open_to_provision", "kind": "time_between",
                    "from_activity": "@case_opened", "to_activity": "@access_provisioned"}},
        {"case_type": "@onboarding_case",
         "metric": {"name": "onboarding_provision_to_activation", "kind": "time_between",
                    "from_activity": "@access_provisioned", "to_activity": "@activation_confirmed"}},
        {"case_type": "@onboarding_case",
         "metric": {"name": "onboarding_open_to_activation", "kind": "time_between",
                    "from_activity": "@case_opened", "to_activity": "@activation_confirmed"}},
        {"case_type": "@onboarding_case",
         "metric": {"name": "onboarding_rework_events", "kind": "activity_count",
                    "activity": "@rework_requested"}},
    ],
    "compliance": [
        {"case_type": "@onboarding_case",
         "rule": {"template": "precedence", "a": "@case_opened", "b": "@access_provisioned"}},
        {"case_type": "@onboarding_case",
         "rule": {"template": "precedence", "a": "@requirements_verified", "b": "@access_provisioned"}},
        {"case_type": "@onboarding_case",
         "rule": {"template": "response_within", "a": "@case_opened", "b": "@access_provisioned",
                  "seconds": "$provision_hours"}},
        {"case_type": "@onboarding_case",
         "rule": {"template": "response_within", "a": "@access_provisioned", "b": "@activation_confirmed",
                  "seconds": "$activation_days"}},
        {"case_type": "@onboarding_case",
         "rule": {"template": "precedence", "a": "@activation_confirmed", "b": "@case_closed"}},
        {"case_type": "@onboarding_case",
         "rule": {"template": "existence", "a": "@activation_confirmed", "min": 0, "max": 1}},
    ],
    "object_rules": [],
    "reference_model": {
        "types": [
            {"object_type": "@onboarding_case",
             "steps": ["@case_opened", {"optional": "@handoff_received"},
                       {"optional": "@requirements_verified"}, "@access_provisioned",
                       "@activation_confirmed", {"optional": "@case_closed"}]},
        ],
        "arcs": [
            {"activity": "@case_opened", "object_type": "@onboarding_case", "variable": False},
            {"activity": "@handoff_received", "object_type": "@onboarding_case", "variable": False},
            {"activity": "@requirements_verified", "object_type": "@onboarding_case", "variable": False},
            {"activity": "@access_provisioned", "object_type": "@onboarding_case", "variable": False},
            {"activity": "@activation_confirmed", "object_type": "@onboarding_case", "variable": False},
            {"activity": "@case_closed", "object_type": "@onboarding_case", "variable": False},
        ],
    },
    "assumptions": [
        "The mapping must use a stable, authorized onboarding case identifier; equal case IDs from "
        "different sources are not evidence that the same customer was involved.",
        "The reference lifecycle shows a simple successful path; cancelled, incomplete, exceptional "
        "or unobserved cases can appear as missing responses or model deviations.",
        "Access provisioned and activation confirmed are event labels asserted by the supplied log. "
        "This pack cannot validate current entitlement, source-of-truth provisioning or customer success.",
        "The prerequisite-order rule applies only when the optional verification step is mapped. "
        "Not every onboarding process requires the same approvals or controls.",
        "A second activation event is flagged for review, not assumed to be a second customer or "
        "incorrect action. Retries, corrections and multi-product activations may be legitimate.",
        "The reference path excludes rework, which is counted separately and may appear as a deviation.",
        "Durations use the first recorded occurrence of mapped activities and include nights, weekends, "
        "waiting and missing history; they are not employee effort or customer conversion rates.",
        "Response rules evaluate each recorded initiating event, including those without an observed "
        "completion. Their targets are editable examples, not validated contractual service levels.",
        "This pack does not import CRM/identity records, join users, grant execution, certify compliance "
        "or independently verify any customer outcome.",
    ],
}


EMPLOYEE_OFFBOARDING_1_0_0: dict[str, Any] = {
    "format": FORMAT, "pack_id": "employee_offboarding", "version": "1.0.0",
    "title": "Employee offboarding and access revocation",
    "reference_goal": "Recorded offboarding initiation to access-revocation and case-review lifecycle",
    "description": (
        "Examine employee offboarding case events: elapsed time to the logged access revocation, "
        "missing/late/out-of-order or duplicate revocation events, optional managerial approval, "
        "asset return, closure and review. Only supplied event evidence is checked, not live account entitlements."
    ),
    "object_types": [
        {"role": "offboarding_case", "label": "Offboarding case", "required": True},
    ],
    "activities": [
        {"role": "offboarding_started", "label": "Start offboarding", "required": True},
        {"role": "manager_approved", "label": "Record manager approval", "required": False},
        {"role": "access_revoked", "label": "Record access revocation", "required": True},
        {"role": "asset_returned", "label": "Record asset return", "required": False},
        {"role": "review_completed", "label": "Record completion review", "required": False},
        {"role": "case_closed", "label": "Close offboarding case", "required": False},
    ],
    "parameters": [
        {"name": "revocation_hours", "label": "Log revocation within (elapsed hours of start)",
         "unit": "hours", "default": 24, "min": 0.25, "max": 8760},
        {"name": "closure_days", "label": "Close case within (elapsed days of start)",
         "unit": "days", "default": 7, "min": 1, "max": 365},
    ],
    "metrics": [
        {"case_type": "@offboarding_case", "metric": {
            "name": "offboarding_start_to_revocation", "kind": "time_between",
            "from_activity": "@offboarding_started", "to_activity": "@access_revoked"}},
        {"case_type": "@offboarding_case", "metric": {
            "name": "offboarding_start_to_close", "kind": "time_between",
            "from_activity": "@offboarding_started", "to_activity": "@case_closed"}},
        {"case_type": "@offboarding_case", "metric": {
            "name": "offboarding_revocation_events", "kind": "activity_count",
            "activity": "@access_revoked"}},
        {"case_type": "@offboarding_case", "metric": {
            "name": "offboarding_start_to_approval", "kind": "time_between",
            "from_activity": "@offboarding_started", "to_activity": "@manager_approved"}},
        {"case_type": "@offboarding_case", "metric": {
            "name": "offboarding_return_events", "kind": "activity_count",
            "activity": "@asset_returned"}},
    ],
    "compliance": [
        {"case_type": "@offboarding_case", "rule": {
            "template": "response_within", "a": "@offboarding_started",
            "b": "@access_revoked", "seconds": "$revocation_hours"}},
        {"case_type": "@offboarding_case", "rule": {
            "template": "precedence", "a": "@offboarding_started", "b": "@access_revoked"}},
        {"case_type": "@offboarding_case", "rule": {
            "template": "precedence", "a": "@manager_approved", "b": "@access_revoked"}},
        {"case_type": "@offboarding_case", "rule": {
            "template": "precedence", "a": "@access_revoked", "b": "@case_closed"}},
        {"case_type": "@offboarding_case", "rule": {
            "template": "existence", "a": "@access_revoked", "min": 1, "max": 1}},
        {"case_type": "@offboarding_case", "rule": {
            "template": "response_within", "a": "@offboarding_started",
            "b": "@case_closed", "seconds": "$closure_days"}},
        {"case_type": "@offboarding_case", "rule": {
            "template": "segregation_of_duties", "a": "@manager_approved",
            "b": "@access_revoked"}},
    ],
    "object_rules": [],
    "reference_model": {
        "types": [
            {"object_type": "@offboarding_case", "steps": [
                "@offboarding_started",
                {"optional": "@manager_approved"},
                "@access_revoked",
                {"optional": "@asset_returned"},
                {"optional": "@review_completed"},
                {"optional": "@case_closed"},
            ]},
        ],
        "arcs": [
            {"activity": "@offboarding_started", "object_type": "@offboarding_case", "variable": False},
            {"activity": "@manager_approved", "object_type": "@offboarding_case", "variable": False},
            {"activity": "@access_revoked", "object_type": "@offboarding_case", "variable": False},
            {"activity": "@asset_returned", "object_type": "@offboarding_case", "variable": False},
            {"activity": "@review_completed", "object_type": "@offboarding_case", "variable": False},
            {"activity": "@case_closed", "object_type": "@offboarding_case", "variable": False},
        ],
    },
    "assumptions": [
        "Every case ID represents one selected offboarding event sequence, as asserted by the supplied mapping; "
        "colliding IDs across accounts, employees, providers or tenants must be separated before import.",
        "A recorded access-revocation activity does NOT prove that every identity provider, device, role, "
        "session, API token, shared credential or third-party application actually revoked access. "
        "The pack does not query any directory, identity provider or live entitlement.",
        "The revocation and closure targets are elapsed wall-clock windows, not validated legal, "
        "contractual or policy deadlines until the owner reviews and configures them.",
        "A missing revocation activity indicates absent evidence in this log, not proof access remains. "
        "Partial exports, asynchronous feeds and source deletion can all produce missing events.",
        "A repeated revocation record is a review candidate, not evidence of duplicated permissions "
        "or a second deprovisioning action. Retries and independent application revocations may be legitimate.",
        "Manager approval, asset return, completed review and closure are optional mapped steps; "
        "only rules and metrics with mapped roles apply. Absence of an unmapped optional role does not "
        "mean policy controls were satisfied or intentionally omitted.",
        "When manager approval is mapped, an access-revocation event lacking an earlier manager-approval "
        "event is flagged for review; emergency or exemption cases may legitimately follow another "
        "authorized procedure. The pack cannot determine those exceptions from activity labels.",
        "Segregation of duties depends on actual recorded actor identities. If resource/actor labels "
        "are absent, the result is unknown and not treated as verified separation.",
        "Timing in supplied event logs is not employee effort saved, verified security compliance, "
        "an authorization to delete accounts, or a source-system proof of business outcomes.",
    ],
}

EXPENSE_REIMBURSEMENT_1_0_0: dict[str, Any] = {
    "format": FORMAT, "pack_id": "expense_reimbursement", "version": "1.0.0",
    "title": "Expense reimbursement",
    "reference_goal": "Selected payable expense claims from submission to recorded reimbursement",
    "description": (
        "Review selected payable expense claims: submission-to-approval and approval-to-payment "
        "elapsed times, missing or repeated payment records, receipt review, returned claims and closure. "
        "Recorded payment is not independently verified settlement."
    ),
    "object_types": [{"role": "claim", "label": "Expense claim", "required": True}],
    "activities": [
        {"role": "submitted", "label": "Submit claim", "required": True},
        {"role": "receipt_checked", "label": "Check receipts", "required": False},
        {"role": "returned", "label": "Return for correction", "required": False},
        {"role": "approved", "label": "Approve claim", "required": True},
        {"role": "paid", "label": "Record reimbursement", "required": True},
        {"role": "closed", "label": "Close claim", "required": False},
    ],
    "parameters": [
        {"name": "approval_days", "label": "Approve within (elapsed days of submission)",
         "unit": "days", "default": 7, "min": 1, "max": 365},
        {"name": "payment_days", "label": "Record payment within (elapsed days of approval)",
         "unit": "days", "default": 14, "min": 1, "max": 365},
    ],
    "metrics": [
        {"case_type": "@claim", "metric": {
            "name": "expense_submission_to_approval", "kind": "time_between",
            "from_activity": "@submitted", "to_activity": "@approved"}},
        {"case_type": "@claim", "metric": {
            "name": "expense_approval_to_payment", "kind": "time_between",
            "from_activity": "@approved", "to_activity": "@paid"}},
        {"case_type": "@claim", "metric": {
            "name": "expense_submission_to_payment", "kind": "time_between",
            "from_activity": "@submitted", "to_activity": "@paid"}},
        {"case_type": "@claim", "metric": {
            "name": "expense_payment_records", "kind": "activity_count", "activity": "@paid"}},
        {"case_type": "@claim", "metric": {
            "name": "expense_return_records", "kind": "activity_count", "activity": "@returned"}},
    ],
    "compliance": [
        {"case_type": "@claim", "rule": {"template": "response_within", "a": "@submitted",
                                           "b": "@approved", "seconds": "$approval_days"}},
        {"case_type": "@claim", "rule": {"template": "response_within", "a": "@approved",
                                           "b": "@paid", "seconds": "$payment_days"}},
        {"case_type": "@claim", "rule": {"template": "precedence", "a": "@submitted", "b": "@approved"}},
        {"case_type": "@claim", "rule": {"template": "precedence", "a": "@approved", "b": "@paid"}},
        {"case_type": "@claim", "rule": {"template": "precedence", "a": "@receipt_checked", "b": "@approved"}},
        {"case_type": "@claim", "rule": {"template": "existence", "a": "@paid", "min": 1, "max": 1}},
        {"case_type": "@claim", "rule": {"template": "segregation_of_duties", "a": "@submitted", "b": "@approved"}},
        {"case_type": "@claim", "rule": {"template": "precedence", "a": "@paid", "b": "@closed"}},
    ],
    "object_rules": [],
    "reference_model": {
        "types": [{"object_type": "@claim", "steps": [
            "@submitted", {"optional": "@returned"}, {"optional": "@receipt_checked"},
            "@approved", "@paid", {"optional": "@closed"}]}],
        "arcs": [{"activity": "@" + role, "object_type": "@claim", "variable": False}
                 for role in ("submitted", "returned", "receipt_checked", "approved", "paid", "closed")],
    },
    "assumptions": [
        "Select payable reimbursement claims only; rejected, withdrawn, corporate-card and advance claims "
        "need a separately reviewed model. Missing payment in an open or partial case is absent evidence, "
        "not a proven overdue obligation. This pack does not infer which claims are payable.",
        "Each supplied case ID denotes one claim in one account; separate colliding account or tenant IDs before import.",
        "A recorded payment does NOT prove bank settlement, correct amount/currency, receipt validity, "
        "tax treatment, fraud detection or verified financial compliance. No banking or expense provider is queried.",
        "Approval and payment targets are configurable elapsed days, not working days or an adopted business policy.",
        "Exactly one payment record is a review assumption: instalments, retries, reversals and split reimbursements "
        "may legitimately create several records. A repeated record does not prove duplicate payment.",
        "Receipt checking, returns and closure apply only when mapped. Receipt checking before approval "
        "does not establish that every line item was checked. Exceptions need independent review.",
        "The reference path allows one return before approval; repeated correction loops and resubmissions "
        "can deviate without being improper. Durations use the existing first-occurrence metric semantics.",
        "Segregation of duties compares recorded submitter and approver resources, not verified employee identities; "
        "missing resources remain unknown. Elapsed time is not employee effort or savings.",
        "Installing or reporting never authorizes approval, payment, source reads or any other business action.",
    ],
}

PACKS: dict[str, dict[str, dict[str, Any]]] = {"returns_refunds": {"1.0.0": RETURNS_REFUNDS_1_0_0},
                                              "order_to_cash": {"1.0.0": ORDER_TO_CASH_1_0_0},
                                              "procure_to_pay": {"1.0.0": PROCURE_TO_PAY_1_0_0},
                                              "service_management": {"1.0.0": SERVICE_MANAGEMENT_1_0_0},
                                              "sales_lead_management": {"1.0.0": SALES_LEAD_1_0_0},
                                              "customer_onboarding": {"1.0.0": CUSTOMER_ONBOARDING_1_0_0},
                                              "employee_offboarding": {"1.0.0": EMPLOYEE_OFFBOARDING_1_0_0},
                                              "expense_reimbursement": {"1.0.0": EXPENSE_REIMBURSEMENT_1_0_0}}


class _Strict(BaseModel):
    # Never persist NaN or Infinity in a policy target/digest. They otherwise
    # bypass ordinary inclusive range checks because comparisons are unordered.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Mapping(_Strict):
    object_types: dict[Annotated[str, Field(max_length=40)], Name] = Field(max_length=40)
    activities: dict[Annotated[str, Field(max_length=40)], Name] = Field(max_length=80)


class DomainPackConflict(workspace.EventLogWorkspaceError):
    """The caller's reviewed installation is no longer current."""


class InstallSpec(_Strict):
    version: Annotated[str, Field(pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")]
    mapping: Mapping
    expected_installation: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    parameters: dict[Annotated[str, Field(max_length=40)], float] = Field(default_factory=dict, max_length=20)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def _pack(pack_id: str, version: str | None = None) -> dict[str, Any]:
    versions = PACKS.get(pack_id)
    if not versions:
        raise workspace.EventLogWorkspaceError("unknown domain pack")
    if version is None:
        version = max(versions, key=_version_key)
    if version not in versions:
        raise workspace.EventLogWorkspaceError(f"domain pack {pack_id} has no version {version}")
    return versions[version]


def catalog() -> dict[str, Any]:
    packs = []
    for pack_id, versions in sorted(PACKS.items()):
        latest = _pack(pack_id)
        packs.append({"pack_id": pack_id, "title": latest["title"], "description": latest["description"],
                      "versions": sorted(versions, key=_version_key), "latest_version": latest["version"],
                      **{k: latest[k] for k in ("object_types", "activities", "parameters", "assumptions")},
                      "definitions": {"metrics": len(latest["metrics"]), "compliance_rules": len(latest["compliance"]),
                                      "object_rules": len(latest["object_rules"]),
                                      "reference_model_types": len(latest["reference_model"]["types"])}})
    return {"format": FORMAT, "packs": packs}


# --- resolution ---------------------------------------------------------------

class _Unmapped(Exception):
    def __init__(self, role: str):
        self.role = role


UNIT_SECONDS = {"days": 86400, "hours": 3600}


def _resolve(value: Any, names: dict[str, str], params: dict[str, float], key: str | None = None) -> Any:
    """Replace ``@role`` with the mapped name and ``$parameter`` with seconds; unmapped roles raise.

    ``params`` holds (value, seconds per unit) pairs; a parameter's unit is days unless the pack says hours."""
    if isinstance(value, dict):
        return {k: _resolve(v, names, params, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, names, params) for v in value]
    if isinstance(value, str) and value.startswith("@"):
        if value[1:] not in names:
            raise _Unmapped(value[1:])
        return names[value[1:]]
    if isinstance(value, str) and value.startswith("$") and key == "seconds":
        amount, unit = params[value[1:]]
        return int(round(amount * unit))
    return value


def _sequence_model(labels: list[tuple[str, bool]], goal: str) -> dict[str, Any]:
    """A catalog process model for a sequence in which (label, optional) steps may be skipped."""
    nodes = [{"id": "start_1", "label": "Start", "kind": "start", "implementation": "unmapped"}]
    edges, rules, tails = [], [], [("start_1", None)]
    for n, (label, optional) in enumerate(labels, 1):
        act = f"act_{n}"
        nodes.append({"id": act, "label": label, "kind": "activity", "implementation": "unmapped"})
        if optional:
            decision, merge = f"decision_{n}", f"merge_{n}"
            nodes += [{"id": decision, "label": f"Decision {n}", "kind": "decision", "implementation": "unmapped"},
                      {"id": merge, "label": f"Merge {n}", "kind": "routing", "implementation": "unmapped"}]
            edges += [{"source": s, "target": decision, "rule_id": r} for s, r in tails]
            take, skip = f"rule_{len(rules) + 1}", f"rule_{len(rules) + 2}"
            rules += [{"id": take, "text": f"Pack assumption, not a confirmed rule: {label} happens when your "
                                           "business requires it. State the real condition.", "user_confirmed": False},
                      {"id": skip, "text": f"Pack assumption, not a confirmed rule: {label} is skipped otherwise.",
                       "user_confirmed": False}]
            edges += [{"source": decision, "target": act, "rule_id": take},
                      {"source": decision, "target": merge, "rule_id": skip},
                      {"source": act, "target": merge, "rule_id": None}]
            tails = [(merge, None)]
        else:
            edges += [{"source": s, "target": act, "rule_id": r} for s, r in tails]
            tails = [(act, None)]
    nodes.append({"id": "end_1", "label": "End", "kind": "end", "implementation": "unmapped"})
    edges += [{"source": s, "target": "end_1", "rule_id": r} for s, r in tails]
    return {"format": "openworkgraph.process-model.v1", "goal": goal, "owner_label": "",
            "baseline_path": [label for label, _ in labels][:40], "baseline_source": "selected_csv_variant",
            "baseline_coverage": "complete_supplied_path", "baseline_support_unit": "declared_cases",
            "supporting_case_count": 0, "activities": nodes, "transitions": edges, "business_rules": rules,
            "entry_activity_id": "start_1", "terminal_activity_ids": ["end_1"], "outcome_expectations": [],
            "manual_review_conditions": [], "opportunity_scenario": None, "observed_path_is_policy": False,
            "execution_authorized": False, "outcomes_verified": False}


def resolve(pack: dict[str, Any], mapping: dict[str, dict[str, str]], params: dict[str, float]) -> dict[str, Any]:
    """Every definition of the pack with the log's names, and the ones an unmapped optional role leaves out."""
    names = {**mapping["object_types"], **mapping["activities"]}
    labels = {a["role"]: a["label"] for a in pack["activities"]}
    units = {p["name"]: UNIT_SECONDS[p.get("unit", "days")] for p in pack["parameters"]}
    params = {name: (value, units[name]) for name, value in params.items()}
    out: dict[str, Any] = {"metrics": [], "compliance": [], "object_rules": [], "not_applicable": []}
    for section in ("metrics", "compliance", "object_rules"):
        for n, item in enumerate(pack[section]):
            try:
                out[section].append(_resolve(item, names, params))
            except _Unmapped as missing:
                out["not_applicable"].append({"section": section, "index": n, "unmapped_role": missing.role})
    types, arcs = [], []
    for n, t in enumerate(pack["reference_model"]["types"]):
        role = t["object_type"][1:]
        if role not in names:
            out["not_applicable"].append({"section": "reference_model", "index": n, "unmapped_role": role})
            continue
        steps = [(s if isinstance(s, str) else s["optional"], not isinstance(s, str)) for s in t["steps"]]
        missing = [s[1:] for s, optional in steps if s[1:] not in names and not optional]
        if missing:
            out["not_applicable"].append({"section": "reference_model", "index": n, "unmapped_role": missing[0]})
            continue
        kept = [(labels[s[1:]], optional) for s, optional in steps if s[1:] in names]
        types.append({"object_type": names[role], "process_model": _sequence_model(kept, pack.get("reference_goal", pack["title"] + " reference lifecycle")),
                      "activities": {labels[s[1:]]: names[s[1:]] for s, _ in steps
                                     if s[1:] in names and labels[s[1:]] != names[s[1:]]}})
    modelled = {t["object_type"] for t in types}
    for arc in pack["reference_model"]["arcs"]:
        try:
            resolved = _resolve(arc, names, params)
        except _Unmapped:
            continue
        if resolved["object_type"] in modelled:
            arcs.append(resolved)
    out["reference_model"] = {"version": object_model.VERSION, "types": types, "arcs": arcs}
    return out


def _digest(definitions: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(definitions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- install -------------------------------------------------------------------

def _parse_install(pack_id: str, spec: Any) -> tuple[dict[str, Any], InstallSpec, dict[str, float]]:
    try:
        parsed = InstallSpec.model_validate(spec)
    except ValidationError as error:
        first = error.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "domain pack install: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    pack = _pack(pack_id, parsed.version)
    for kind, roles, noun in (("object_types", pack["object_types"], "object type"),
                              ("activities", pack["activities"], "activity")):
        known = {r["role"] for r in roles}
        given = getattr(parsed.mapping, kind)
        stray = sorted(set(given) - known)
        if stray:
            raise workspace.EventLogWorkspaceError(f"the pack has no {noun} role {stray[0]}")
        absent = [r["role"] for r in roles if r["required"] and r["role"] not in given]
        if absent:
            raise workspace.EventLogWorkspaceError(f"map the required role {absent[0]} to a name in this log")
        if len(set(given.values())) != len(given):
            raise workspace.EventLogWorkspaceError(f"two {noun} roles are mapped to the same name")
    params = {p["name"]: float(p["default"]) for p in pack["parameters"]}
    for name, value in parsed.parameters.items():
        spec_ = next((p for p in pack["parameters"] if p["name"] == name), None)
        if spec_ is None:
            raise workspace.EventLogWorkspaceError(f"the pack has no parameter {name}")
        if not spec_["min"] <= value <= spec_["max"]:
            raise workspace.EventLogWorkspaceError(f"{name} must be from {spec_['min']} to {spec_['max']}")
        params[name] = value
    return pack, parsed, params


def _check_names(conn, log_id: str, mapping: Mapping) -> None:
    # Bound returned rows to the explicitly mapped labels; an event log can
    # contain far more distinct names than a pack can map. Values remain bound
    # parameters, and the log predicate prevents cross-log matches.
    types = {r[0] for r in conn.execute(
        "SELECT DISTINCT object_type FROM objects WHERE log_id = ? AND object_type IN (SELECT unnest(?))",
        [log_id, list(mapping.object_types.values())]).fetchall()}
    activities = {r[0] for r in conn.execute(
        "SELECT DISTINCT activity FROM events WHERE log_id = ? AND activity IN (SELECT unnest(?))",
        [log_id, list(mapping.activities.values())]).fetchall()}
    for role, name in mapping.object_types.items():
        if name not in types:
            raise workspace.EventLogWorkspaceError(f"{role} is mapped to {name!r}, which is not an object type of this log")
    for role, name in mapping.activities.items():
        if name not in activities:
            raise workspace.EventLogWorkspaceError(f"{role} is mapped to {name!r}, which is not an activity of this log")


def _installation_token(record: dict[str, Any]) -> str:
    """Opaque equality token for the complete stored installation, including owned IDs."""
    return _digest(record)


def _record(conn, log_id: str, pack_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT definition FROM domain_pack_installs WHERE log_id = ? AND pack_id = ?",
                       [log_id, pack_id]).fetchone()
    return json.loads(row[0]) if row else None


def _insert_pack_metric(conn, log_id: str, metric: dict[str, Any], now: datetime) -> str:
    """Use the metric library's schema, but write inside the pack transaction."""
    try:
        validated = query.SavedMetric.model_validate({"metric": metric}).metric
    except ValidationError:
        raise workspace.EventLogWorkspaceError("domain pack contains an invalid case metric") from None
    metric_id = "met_" + secrets.token_hex(8)
    definition = {"format": query.METRIC_FORMAT, "metric": validated.model_dump()}
    conn.execute("INSERT INTO metric_definitions VALUES (?, ?, ?, ?)",
                 [log_id, metric_id, now, json.dumps(definition, sort_keys=True)])
    return metric_id


def install(log_id: str, pack_id: str, spec: Any) -> dict[str, Any]:
    pack, parsed, params = _parse_install(pack_id, spec)
    mapping = parsed.mapping.model_dump()
    definitions = resolve(pack, mapping, params)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("BEGIN TRANSACTION")
            workspace._check_log(conn, log_id)
            _check_names(conn, log_id, parsed.mapping)
            previous = _record(conn, log_id, pack_id)
            if "expected_installation" in parsed.model_fields_set:
                current = _installation_token(previous) if previous is not None else None
                if parsed.expected_installation != current:
                    raise DomainPackConflict("domain pack installation changed; reload setup before saving")
            saved = {json.loads(d)["metric"]["name"]: m for m, d in conn.execute(
                "SELECT metric_id, definition FROM metric_definitions WHERE log_id = ?", [log_id]).fetchall()}
            if previous and _version_key(parsed.version) < _version_key(previous["version"]):
                raise workspace.EventLogWorkspaceError(
                    f"version {previous['version']} is installed; downgrading to {parsed.version} is refused")
            owned = set(previous["metric_ids"]) if previous else set()
            clash = [n for n, m in saved.items() if m not in owned
                     and any(d["metric"]["name"] == n for d in definitions["metrics"])]
            if clash:
                raise workspace.EventLogWorkspaceError(f"a saved case metric named {clash[0]} exists and is not this pack's")
            if len(saved) - len(owned & set(saved.values())) + len(definitions["metrics"]) > query.MAX_SAVED:
                raise workspace.EventLogWorkspaceError(
                    f"a log can hold at most {query.MAX_SAVED} saved case metrics")
            for metric_id in owned & set(saved.values()):
                conn.execute("DELETE FROM metric_definitions WHERE log_id = ? AND metric_id = ?", [log_id, metric_id])
            metric_ids = [_insert_pack_metric(conn, log_id, d["metric"], now) for d in definitions["metrics"]]
            change = ("installed" if not previous else "upgraded" if parsed.version != previous["version"] else "reconfigured")
            history = ([*previous["history"], {k: previous[k] for k in ("version", "installed_at", "definitions_sha256")}]
                       if previous else [])[-MAX_HISTORY:]
            record = {"format": INSTALL_FORMAT, "pack_id": pack_id, "version": parsed.version,
                      "installed_at": now.isoformat() + "Z", "mapping": mapping, "parameters": params,
                      "definitions_sha256": _digest(definitions), "metric_ids": metric_ids, "history": history}
            conn.execute("DELETE FROM domain_pack_installs WHERE log_id = ? AND pack_id = ?", [log_id, pack_id])
            conn.execute("INSERT INTO domain_pack_installs VALUES (?, ?, ?, ?)",
                         [log_id, pack_id, now, json.dumps(record, sort_keys=True)])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    return {**record, "installation_token": _installation_token(record), "change": change, "previous_version": previous["version"] if previous else None,
            "not_applicable": definitions["not_applicable"]}


def installed(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("SELECT definition FROM domain_pack_installs WHERE log_id = ? ORDER BY pack_id",
                                [log_id]).fetchall()
            # The names a mapping can choose from, most frequent first.
            types = [r[0] for r in conn.execute("""SELECT object_type FROM objects WHERE log_id = ?
                GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT ?""", [log_id, MAX_NAMES + 1]).fetchall()]
            activities = [r[0] for r in conn.execute("""SELECT activity FROM events WHERE log_id = ?
                GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT ?""", [log_id, MAX_NAMES + 1]).fetchall()]
        finally:
            conn.close()
    installs = [json.loads(d) for (d,) in rows]
    for record in installs:
        record["installation_token"] = _installation_token(record)
        record["latest_version"] = _pack(record["pack_id"])["version"] if record["pack_id"] in PACKS else None
        record["upgrade_available"] = bool(record["latest_version"]) and \
            _version_key(record["latest_version"]) > _version_key(record["version"])
    return {"installs": installs, "log_object_types": types[:MAX_NAMES], "log_activities": activities[:MAX_NAMES],
            "names_truncated": len(types) > MAX_NAMES or len(activities) > MAX_NAMES}


def uninstall(log_id: str, pack_id: str, *, expected_installation: str | None = None) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("BEGIN TRANSACTION")
            workspace._check_log(conn, log_id)
            previous = _record(conn, log_id, pack_id)
            if expected_installation is not None:
                current = _installation_token(previous) if previous is not None else None
                if expected_installation != current:
                    raise DomainPackConflict("domain pack installation changed; reload setup before removing")
            if previous is None:
                raise workspace.EventLogWorkspaceError("this domain pack is not installed on the log")
            owned = set(previous["metric_ids"]) & {m for (m,) in conn.execute(
                "SELECT metric_id FROM metric_definitions WHERE log_id = ?", [log_id]).fetchall()}
            conn.execute("DELETE FROM domain_pack_installs WHERE log_id = ? AND pack_id = ?", [log_id, pack_id])
            for metric_id in owned:
                conn.execute("DELETE FROM metric_definitions WHERE log_id = ? AND metric_id = ?", [log_id, metric_id])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    return {"uninstalled": True, "pack_id": pack_id, "version": previous["version"], "metrics_removed": len(owned)}


# --- report --------------------------------------------------------------------

def _metric_summary(log_id: str, item: dict[str, Any]) -> dict[str, Any]:
    name = item["metric"]["name"]
    result = query.run(log_id, {"level": "cases", "case_type": item["case_type"], "metrics": [item["metric"]],
                                "measures": [{"fn": "cases"}, {"fn": "median", "of": "metric:" + name},
                                             {"fn": "p90", "of": "metric:" + name},
                                             {"fn": "avg", "of": "metric:" + name}]})
    row = result["rows"][0] if result["rows"] else [0, None, None, None]
    coverage = result["metrics"][0]
    return {"name": name, "case_type": item["case_type"], "definition": item["metric"], "unit": coverage["unit"],
            "cases": row[0], "median": row[1], "p90": row[2], "mean": row[3],
            "coverage": {k: v for k, v in coverage.items() if k not in ("name", "definition", "unit")}}


def report(log_id: str, pack_id: str) -> dict[str, Any]:
    with workspace._LOCK:       # re-entrant: every section reads the same log revision
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            record = _record(conn, log_id, pack_id)
        finally:
            conn.close()
        if record is None:
            raise workspace.EventLogWorkspaceError("install this domain pack on the log before running its report")
        pack = _pack(pack_id, record["version"])
        definitions = resolve(pack, record["mapping"], record["parameters"])
        if _digest(definitions) != record["definitions_sha256"]:
            raise workspace.EventLogWorkspaceError("the installed definitions no longer match their digest; reinstall the pack")
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in definitions["compliance"]:
            groups.setdefault(item["case_type"], []).append(item["rule"])
        sections = {
            "metrics": [_metric_summary(log_id, item) for item in definitions["metrics"]],
            "compliance": [{"case_type": t, **{k: v for k, v in compliance.check(log_id, {"rules": rules, "case_type": t}).items()
                                              if k in ("results", "rules_with_violations")}} for t, rules in groups.items()],
            "object_rules": ({k: v for k, v in object_constraints.check(log_id, {"rules": definitions["object_rules"]}).items()
                              if k in ("results", "rules_with_violations", "rules_unknown")}
                             if definitions["object_rules"] else None),
            "object_model": ({k: v for k, v in object_model.check(log_id, {"model": definitions["reference_model"]}).items()
                              if k in ("lifecycles", "arcs", "summary")} if definitions["reference_model"]["types"] else None),
        }
    return {
        "log": info, "pack": {"pack_id": pack_id, "version": record["version"], "title": pack["title"],
                              "definitions_sha256": record["definitions_sha256"], "installed_at": record["installed_at"]},
        "mapping": record["mapping"], "parameters": record["parameters"], **sections,
        "not_applicable": definitions["not_applicable"], "assumptions": pack["assumptions"],
        "interpretation": ("Pack definitions applied to this log through your mapping. They state assumptions about a "
                           f"typical {pack['title'].lower()} process, not your policy until you set the parameters, and are not "
                           "validated against any particular source system. Results describe the log, not verified "
                           "business outcomes or compliance."),
        "source_system_validated": False, "policy_compliance_verified": False,
        **workspace.DESCRIPTIVE_ONLY,
    }


def definitions(pack_id: str, version: str | None = None) -> dict[str, Any]:
    """The pack as published, for review before installing."""
    return copy.deepcopy(_pack(pack_id, version))


__all__ = ["FORMAT", "PACKS", "catalog", "definitions", "install", "installed", "report", "resolve", "uninstall"]
