"""A deterministic synthetic procure-to-pay OCEL 2.0 log with known deviations, for the domain pack tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

START = datetime(2024, 5, 6, 8, tzinfo=timezone.utc)
NAMES = {"create_requisition": "ME51N Create PR", "approve_requisition": "ME54N Release PR",
         "create_po": "ME21N Create PO", "approve_po": "ME29N Release PO", "change_po": "ME22N Change PO",
         "receive_goods": "MIGO Goods Receipt", "post_invoice": "MIRO Invoice", "pay_invoice": "F110 Payment"}
TYPES = {"requisition": "PurchaseRequisition", "purchase_order": "PurchaseOrder", "goods_receipt": "GoodsReceipt",
         "invoice": "SupplierInvoice", "payment": "Payment"}


def procure_to_pay(orders: int = 30) -> dict:
    """Purchase order k comes from requisition k and has one goods receipt, one invoice and one payment.

    Deviations, by order number:
    - k % 7 == 3: the requisition is never approved (3, 10, 17, 24);
    - k % 6 == 5: the order is never approved, yet goods are received (5, 11, 17, 23, 29);
    - k % 10 == 4: goods arrive after 20 days (4, 14, 24; target 14) and are invoiced two days later;
    - k % 10 == 8: the invoice is posted before the goods receipt (8, 18, 28);
    - k == 12: the invoice is paid twice; k == 20: it is never paid;
    - k % 5 == 2: the order is changed once;
    - one more invoice, ``inv-x``, is posted and paid without any purchase order.
    """
    objects, events = [], []
    rel = lambda ids: [{"objectId": i, "qualifier": ""} for i in ids]

    def event(eid, role, when, ids):
        events.append({"id": eid, "type": NAMES[role], "time": when.isoformat().replace("+00:00", "Z"),
                       "relationships": rel(ids)})

    def obj(oid, role):
        objects.append({"id": oid, "type": TYPES[role]})
    for k in range(orders):
        t = START + timedelta(days=k)
        rq, po, gr, inv = f"rq{k}", f"po{k}", f"gr{k}", f"inv{k}"
        for oid, role in ((rq, "requisition"), (po, "purchase_order"), (gr, "goods_receipt"), (inv, "invoice")):
            obj(oid, role)
        event(f"{rq}-create", "create_requisition", t - timedelta(days=2), [rq])
        if k % 7 != 3:
            event(f"{rq}-release", "approve_requisition", t - timedelta(days=1), [rq])
        event(f"{po}-create", "create_po", t, [po, rq])
        if k % 6 != 5:
            event(f"{po}-release", "approve_po", t + timedelta(hours=2), [po])
        if k % 5 == 2:
            event(f"{po}-change", "change_po", t + timedelta(hours=3), [po])
        event(f"{gr}-migo", "receive_goods", t + timedelta(days=20 if k % 10 == 4 else 7), [gr, po])
        event(f"{inv}-miro", "post_invoice", t + timedelta(days=5 if k % 10 == 8 else 9) if k % 10 != 4
              else t + timedelta(days=22), [inv, po])
        if k != 20:
            obj(f"pm{k}", "payment")
            event(f"pm{k}-f110", "pay_invoice", t + timedelta(days=30), [f"pm{k}", inv])
        if k == 12:
            obj("pm12b", "payment")
            event("pm12b-f110", "pay_invoice", t + timedelta(days=31), ["pm12b", inv])
    t = START + timedelta(days=orders)
    obj("inv-x", "invoice")
    obj("pm-x", "payment")
    event("inv-x-miro", "post_invoice", t, ["inv-x"])
    event("pm-x-f110", "pay_invoice", t + timedelta(days=20), ["pm-x", "inv-x"])
    return {"objectTypes": [{"name": n, "attributes": []} for n in TYPES.values()],
            "eventTypes": [{"name": n, "attributes": []} for n in NAMES.values()],
            "objects": objects, "events": events}


MAPPING = {"object_types": dict(TYPES), "activities": dict(NAMES)}
