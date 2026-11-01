"""Versioned domain packs for saved event logs: order to cash and procure to pay (#370 R23).

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
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_compliance as compliance
from . import event_log_object_constraints as object_constraints
from . import event_log_object_process_model as object_model
from . import event_log_query as query
from . import event_log_workspace as workspace

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

PACKS: dict[str, dict[str, dict[str, Any]]] = {"order_to_cash": {"1.0.0": ORDER_TO_CASH_1_0_0},
                                              "procure_to_pay": {"1.0.0": PROCURE_TO_PAY_1_0_0}}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Mapping(_Strict):
    object_types: dict[Annotated[str, Field(max_length=40)], Name] = Field(max_length=40)
    activities: dict[Annotated[str, Field(max_length=40)], Name] = Field(max_length=80)


class InstallSpec(_Strict):
    version: Annotated[str, Field(pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")]
    mapping: Mapping
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


def _resolve(value: Any, names: dict[str, str], params: dict[str, float], key: str | None = None) -> Any:
    """Replace ``@role`` with the mapped name and ``$parameter`` (days) with seconds; unmapped roles raise."""
    if isinstance(value, dict):
        return {k: _resolve(v, names, params, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, names, params) for v in value]
    if isinstance(value, str) and value.startswith("@"):
        if value[1:] not in names:
            raise _Unmapped(value[1:])
        return names[value[1:]]
    if isinstance(value, str) and value.startswith("$") and key == "seconds":
        return int(round(params[value[1:]] * 86400))
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
    types = {r[0] for r in conn.execute("SELECT DISTINCT object_type FROM objects WHERE log_id = ?", [log_id]).fetchall()}
    activities = {r[0] for r in conn.execute("SELECT DISTINCT activity FROM events WHERE log_id = ?", [log_id]).fetchall()}
    for role, name in mapping.object_types.items():
        if name not in types:
            raise workspace.EventLogWorkspaceError(f"{role} is mapped to {name!r}, which is not an object type of this log")
    for role, name in mapping.activities.items():
        if name not in activities:
            raise workspace.EventLogWorkspaceError(f"{role} is mapped to {name!r}, which is not an activity of this log")


def _record(conn, log_id: str, pack_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT definition FROM domain_pack_installs WHERE log_id = ? AND pack_id = ?",
                       [log_id, pack_id]).fetchone()
    return json.loads(row[0]) if row else None


def install(log_id: str, pack_id: str, spec: Any) -> dict[str, Any]:
    pack, parsed, params = _parse_install(pack_id, spec)
    mapping = parsed.mapping.model_dump()
    definitions = resolve(pack, mapping, params)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            _check_names(conn, log_id, parsed.mapping)
            previous = _record(conn, log_id, pack_id)
            saved = {json.loads(d)["metric"]["name"]: m for m, d in conn.execute(
                "SELECT metric_id, definition FROM metric_definitions WHERE log_id = ?", [log_id]).fetchall()}
        finally:
            conn.close()
        if previous and _version_key(parsed.version) < _version_key(previous["version"]):
            raise workspace.EventLogWorkspaceError(
                f"version {previous['version']} is installed; downgrading to {parsed.version} is refused")
        owned = set(previous["metric_ids"]) if previous else set()
        clash = [n for n, m in saved.items() if m not in owned and any(d["metric"]["name"] == n for d in definitions["metrics"])]
        if clash:
            raise workspace.EventLogWorkspaceError(f"a saved case metric named {clash[0]} exists and is not this pack's")
        for metric_id in owned & set(saved.values()):
            query.delete_metric(log_id, metric_id)
        metric_ids = [query.save_metric(log_id, {"metric": d["metric"]})["metric_id"] for d in definitions["metrics"]]
        change = ("installed" if not previous else "upgraded" if parsed.version != previous["version"] else "reconfigured")
        history = ([*previous["history"], {k: previous[k] for k in ("version", "installed_at", "definitions_sha256")}]
                   if previous else [])[-MAX_HISTORY:]
        record = {"format": INSTALL_FORMAT, "pack_id": pack_id, "version": parsed.version,
                  "installed_at": now.isoformat() + "Z", "mapping": mapping, "parameters": params,
                  "definitions_sha256": _digest(definitions), "metric_ids": metric_ids, "history": history}
        conn = workspace._connect()
        try:
            conn.execute("DELETE FROM domain_pack_installs WHERE log_id = ? AND pack_id = ?", [log_id, pack_id])
            conn.execute("INSERT INTO domain_pack_installs VALUES (?, ?, ?, ?)",
                         [log_id, pack_id, now, json.dumps(record, sort_keys=True)])
        finally:
            conn.close()
    return {**record, "change": change, "previous_version": previous["version"] if previous else None,
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
        record["latest_version"] = _pack(record["pack_id"])["version"] if record["pack_id"] in PACKS else None
        record["upgrade_available"] = bool(record["latest_version"]) and \
            _version_key(record["latest_version"]) > _version_key(record["version"])
    return {"installs": installs, "log_object_types": types[:MAX_NAMES], "log_activities": activities[:MAX_NAMES],
            "names_truncated": len(types) > MAX_NAMES or len(activities) > MAX_NAMES}


def uninstall(log_id: str, pack_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            previous = _record(conn, log_id, pack_id)
            if previous is None:
                raise workspace.EventLogWorkspaceError("this domain pack is not installed on the log")
            owned = set(previous["metric_ids"]) & {m for (m,) in conn.execute(
                "SELECT metric_id FROM metric_definitions WHERE log_id = ?", [log_id]).fetchall()}
            conn.execute("DELETE FROM domain_pack_installs WHERE log_id = ? AND pack_id = ?", [log_id, pack_id])
        finally:
            conn.close()
        for metric_id in owned:
            query.delete_metric(log_id, metric_id)
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
