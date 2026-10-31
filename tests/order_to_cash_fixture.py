"""A deterministic synthetic order-to-cash OCEL 2.0 log with known deviations, for the domain pack tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
NAMES = {"create_order": "VA01 Create Sales Order", "approve_credit": "Credit Release", "change_order": "VA02 Change",
         "pick_item": "Pick", "ship": "Post Goods Issue", "create_invoice": "VF01 Billing", "receive_payment": "Clear Payment"}
TYPES = {"order": "SalesOrder", "item": "OrderItem", "delivery": "Delivery", "invoice": "BillingDoc", "payment": "Payment"}


def order_to_cash(orders: int = 20) -> dict:
    """Order k has 1 + k % 3 items and one delivery. Deviations, by order number:

    - k % 10 == 3: invoiced 20 days after ordering (target 14);
    - k % 10 == 5: credit approved by the clerk who created the order;
    - k % 10 == 7: one item shipped without being picked;
    - k == 9: the invoice is never paid;
    - k % 4 == 0: the order is changed once; k % 5 == 1 is credit-approved by a second person.
    """
    objects, events = [], []
    rel = lambda ids: [{"objectId": i, "qualifier": ""} for i in ids]

    def event(eid, role, when, ids, resource):
        events.append({"id": eid, "type": NAMES[role], "time": when.isoformat().replace("+00:00", "Z"),
                       "attributes": [{"name": "org:resource", "value": resource}], "relationships": rel(ids)})
    for k in range(orders):
        t = START + timedelta(days=k)
        order, delivery, invoice, payment = f"so{k}", f"dl{k}", f"bd{k}", f"pm{k}"
        items = [f"so{k}-{n}" for n in range(1 + k % 3)]
        objects += [{"id": order, "type": TYPES["order"]}, {"id": delivery, "type": TYPES["delivery"]},
                    {"id": invoice, "type": TYPES["invoice"]}] + [{"id": i, "type": TYPES["item"]} for i in items]
        event(f"{order}-create", "create_order", t, [order, *items], "clerk-a")
        if k % 5 == 1 or k % 10 == 5:
            event(f"{order}-credit", "approve_credit", t + timedelta(hours=2), [order],
                  "clerk-a" if k % 10 == 5 else "credit-b")
        if k % 4 == 0:
            event(f"{order}-change", "change_order", t + timedelta(hours=3), [order], "clerk-a")
        for n, item in enumerate(items):
            if not (k % 10 == 7 and n == 0):
                event(f"{item}-pick", "pick_item", t + timedelta(days=1, hours=n), [item], "picker")
        event(f"{delivery}-pgi", "ship", t + timedelta(days=2), [delivery, *items], "warehouse")
        event(f"{invoice}-bill", "create_invoice", t + timedelta(days=20 if k % 10 == 3 else 3), [invoice, order], "billing")
        if k != 9:
            objects.append({"id": payment, "type": TYPES["payment"]})
            event(f"{payment}-clear", "receive_payment", t + timedelta(days=25), [payment, invoice], "ar")
    return {"objectTypes": [{"name": n, "attributes": []} for n in TYPES.values()],
            "eventTypes": [{"name": n, "attributes": [{"name": "org:resource", "type": "string"}]} for n in NAMES.values()],
            "objects": objects, "events": events}


MAPPING = {"object_types": dict(TYPES), "activities": dict(NAMES)}
