"""A synthetic order-to-cash sample log for trying the workspace without your own export.

The sample is generated on this computer from a fixed seed, so it is the same
every time and contains no real customers, people or amounts. It is an OCEL 2.0
log with orders, items, deliveries, invoices and payments, using the activity
names of the order-to-cash domain pack, and it has patterns worth finding:

* most time is spent waiting for payment after the invoice;
* about a fifth of orders are changed, some twice (rework);
* about one order in ten goes on credit hold, which adds days (a costly detour);
* picking slows down in the most recent third of orders (a drift);
* some invoices are still unpaid at the end of the log (open cases).

The log is saved under a name that says it is synthetic, and it is stored and
deleted like any other log.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-sample.v1"
NAME = "Sample: order to cash (synthetic)"
SEED = 20261113
ORDERS = 400
START = datetime(2026, 1, 5, 8, tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def ocel_document(orders: int = ORDERS, seed: int = SEED) -> dict[str, Any]:
    """The sample as an OCEL 2.0 JSON document (deterministic for a given seed)."""
    rng = random.Random(seed)
    objects: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    counter = 0

    def event(activity: str, at: datetime, links: list[tuple[str, str]], attributes: dict[str, Any] | None = None):
        nonlocal counter
        counter += 1
        events.append({"id": f"e{counter:06d}", "type": activity, "time": _iso(at),
                       "attributes": [{"name": k, "value": v} for k, v in sorted((attributes or {}).items())],
                       "relationships": [{"objectId": o, "qualifier": q} for o, q in links]})

    hours = lambda lo, hi: timedelta(hours=rng.uniform(lo, hi))
    for i in range(orders):
        order = f"order-{i + 1:04d}"
        created = START + timedelta(hours=i * 9 + rng.uniform(0, 6))
        region = rng.choice(["EMEA", "AMER", "APAC"])
        amount = round(rng.lognormvariate(7.6, 0.7), 2)
        objects.append({"id": order, "type": "order", "attributes": [
            {"name": "region", "time": _iso(created), "value": region},
            {"name": "segment", "time": _iso(created), "value": rng.choice(["retail", "wholesale"])}]})
        items = [f"{order}-item-{k + 1}" for k in range(rng.choice([1, 1, 2, 2, 3]))]
        for item in items:
            objects.append({"id": item, "type": "item", "attributes": [
                {"name": "product", "time": _iso(created), "value": rng.choice(["P-100", "P-200", "P-300", "P-400"])}]})
        event("Create order", created, [(order, "order")] + [(item, "item") for item in items],
              {"amount": amount, "channel": rng.choice(["web", "web", "phone", "email"])})
        t = created
        if amount > 4000 or rng.random() < 0.25:
            t += hours(1, 10)
            event("Approve credit", t, [(order, "order")])
        if rng.random() < 0.1:
            t += hours(2, 6)
            event("Put on credit hold", t, [(order, "order")])
            t += timedelta(days=rng.uniform(3, 7))
            event("Release credit hold", t, [(order, "order")])
        changes = 2 if rng.random() < 0.06 else (1 if rng.random() < 0.15 else 0)
        for _ in range(changes):
            t += hours(2, 30)
            event("Change order", t, [(order, "order")] + [(item, "item") for item in items])
        slow = i >= orders * 2 // 3
        last_pick = t
        for item in items:
            picked = t + hours(4, 40) + (timedelta(hours=rng.uniform(24, 60)) if slow else timedelta())
            event("Pick item", picked, [(item, "item")])
            last_pick = max(last_pick, picked)
        delivery = f"{order}-delivery"
        objects.append({"id": delivery, "type": "delivery", "attributes": [
            {"name": "carrier", "time": _iso(last_pick), "value": rng.choice(["Carrier A", "Carrier B"])}]})
        shipped = last_pick + hours(2, 20)
        event("Ship", shipped, [(delivery, "delivery"), (order, "order")] + [(item, "item") for item in items])
        invoice = f"{order}-invoice"
        objects.append({"id": invoice, "type": "invoice", "attributes": []})
        invoiced = shipped + hours(1, 72)
        event("Create invoice", invoiced, [(invoice, "invoice"), (order, "order")], {"amount": amount})
        if i < orders - 25 or rng.random() < 0.3:  # the newest invoices are often still open
            payment = f"{order}-payment"
            objects.append({"id": payment, "type": "payment", "attributes": []})
            paid = invoiced + timedelta(days=rng.uniform(5, 50) + (20 if rng.random() < 0.08 else 0))
            event("Receive payment", paid, [(payment, "payment"), (invoice, "invoice"), (order, "order")],
                  {"amount": amount})
    activity_names = sorted({e["type"] for e in events})
    return {
        "objectTypes": [{"name": name, "attributes": []} for name in ["delivery", "invoice", "item", "order", "payment"]],
        "eventTypes": [{"name": name, "attributes": []} for name in activity_names],
        "objects": objects, "events": events,
    }


def import_sample() -> dict[str, Any]:
    """Import the sample, or return the existing one if a log with the sample's name is already saved."""
    existing = next((log for log in workspace.list_logs()["logs"] if log["name"] == NAME), None)
    if existing is not None:
        return {"format": FORMAT, "log": existing, "already_present": True, "synthetic": True}
    document = ocel_document()
    result = workspace.import_log(BytesIO(json.dumps(document).encode()), name=NAME, source_format="ocel")
    return {"format": FORMAT, "log": {k: v for k, v in result.items() if k != "quality"}, "already_present": False,
            "synthetic": True, "suggested_case_notion": "order",
            "about": ("Synthetic order-to-cash data generated on this computer from a fixed seed: no real customers, "
                      "people or amounts. Orders, items, deliveries, invoices and payments, with order changes, "
                      "credit holds, slower recent picking and some unpaid invoices to find.")}
