"""SAP S/4HANA order-to-cash extraction over standard OData v2 APIs (#370 R3).

Read-only. Three standard services are read:

* ``API_SALES_ORDER_SRV/A_SalesOrder``: sales orders;
* ``API_OUTBOUND_DELIVERY_SRV;v=0002/A_OutbDeliveryHeader``: deliveries, with the
  items that name the referenced sales orders;
* ``API_BILLING_DOCUMENT_SRV/A_BillingDocument``: billing documents, with the items
  that name the referenced deliveries and sales orders.

The result is an OCEL 2.0 document with ``sales_order``, ``delivery`` and
``billing_document`` objects. Document numbers and user names are replaced by
keyed pseudonyms (HMAC with an installation secret). Customer fields are never
selected, and amounts are left out unless ``include_amounts`` is set. Cancellation
is an event; SAP's standard APIs do not report physically deleted documents, so
those cannot be detected here.

This module only plans requests, parses responses and projects records. The
transport (TLS, retries, credentials) lives in ``server.sap_o2c_sync``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlsplit

FORMAT = "openworkgraph.sap-o2c-extract.v1"
PAGE_SIZE = 500
MAX_PAGES = 200
MAX_RECORDS = 50000


@dataclass(frozen=True)
class EntitySet:
    key: str
    service: str
    entity: str
    id_field: str
    changed_field: str
    changed_type: str  # "datetimeoffset" or "datetime"
    select: tuple[str, ...]
    expand: str = ""
    item_select: tuple[str, ...] = ()


ENTITY_SETS = (
    EntitySet("sales_orders", "API_SALES_ORDER_SRV", "A_SalesOrder", "SalesOrder", "LastChangeDateTime",
              "datetimeoffset", ("SalesOrder", "SalesOrderType", "CreationDate", "LastChangeDateTime",
                                 "OverallSDProcessStatus", "CreatedByUser", "TotalNetAmount", "TransactionCurrency"),
              "to_Item", ("SalesOrderItem",)),
    EntitySet("deliveries", "API_OUTBOUND_DELIVERY_SRV;v=0002", "A_OutbDeliveryHeader", "DeliveryDocument",
              "LastChangeDate", "datetime", ("DeliveryDocument", "CreationDate", "CreationTime",
                                             "PickingDate", "PickingTime", "ActualGoodsMovementDate",
                                             "ActualGoodsMovementTime", "LastChangeDate",
                                             "OverallGoodsMovementStatus", "CreatedByUser"),
              "to_DeliveryDocumentItem", ("ReferenceSDDocument", "ReferenceSDDocumentItem")),
    EntitySet("billing_documents", "API_BILLING_DOCUMENT_SRV", "A_BillingDocument", "BillingDocument",
              "LastChangeDateTime", "datetimeoffset", ("BillingDocument", "CreationDate", "CreationTime",
                                                       "LastChangeDateTime", "BillingDocumentIsCancelled",
                                                       "CreatedByUser", "TotalNetAmount", "TransactionCurrency"),
              "to_Item", ("ReferenceSDDocument", "SalesDocument")),
)
AMOUNT_FIELDS = {"TotalNetAmount", "TransactionCurrency"}


class SapError(ValueError):
    """Safe error: never includes credentials, document numbers or response bodies."""


def _odata_literal(kind: str, moment: datetime) -> str:
    moment = moment.astimezone(timezone.utc)
    if kind == "datetimeoffset":
        return "datetimeoffset'" + moment.strftime("%Y-%m-%dT%H:%M:%SZ") + "'"
    return "datetime'" + moment.strftime("%Y-%m-%dT%H:%M:%S") + "'"


def first_url(base: str, entity: EntitySet, since: datetime, *, include_amounts: bool) -> str:
    fields = [f for f in entity.select if include_amounts or f not in AMOUNT_FIELDS]
    if entity.expand:
        fields += [f"{entity.expand}/{f}" for f in entity.item_select]
    query = {"$format": "json", "$top": str(PAGE_SIZE), "$inlinecount": "none",
             "$select": ",".join(fields), "$orderby": f"{entity.changed_field},{entity.id_field}",
             "$filter": f"{entity.changed_field} ge {_odata_literal(entity.changed_type, since)}"}
    if entity.expand:
        query["$expand"] = entity.expand
    encoded = "&".join(f"{k}={quote(v, safe=',/()=')}" for k, v in query.items())
    return f"{base.rstrip('/')}/{entity.service}/{entity.entity}?{encoded}"


def metadata_url(base: str, entity: EntitySet) -> str:
    return f"{base.rstrip('/')}/{entity.service}/$metadata"


def validate_base(base: str) -> str:
    """HTTPS service root such as https://my-host.s4hana.ondemand.com/sap/opu/odata/sap."""
    if not isinstance(base, str) or len(base) > 300:
        raise SapError("the SAP OData service root must be a short HTTPS URL")
    parts = urlsplit(base)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise SapError("the SAP OData service root must be an HTTPS URL without credentials or query")
    return base.rstrip("/")


_DATE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")
_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_date(value: Any) -> datetime | None:
    """OData v2 JSON dates: ``/Date(ms)/`` or ``/Date(ms+0000)/``, always read as UTC instants."""
    if not isinstance(value, str):
        return None
    match = _DATE.fullmatch(value)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)


def with_time(day: datetime | None, duration: Any) -> tuple[datetime | None, str]:
    """Combine an Edm.DateTime date with an Edm.Time ``PTxxHxxMxxS``; returns (moment, precision)."""
    if day is None:
        return None, "unknown"
    match = _DURATION.fullmatch(duration) if isinstance(duration, str) else None
    if not match or not any(match.groups()):
        return day, "day"
    h, m, s = (int(x or 0) for x in match.groups())
    return day + timedelta(hours=h, minutes=m, seconds=s), "second"


def page(payload: Any, base: str) -> tuple[list[dict[str, Any]], str | None]:
    """Results and the next-page URL from an OData v2 JSON response; the next link must stay on the same host."""
    try:
        body = payload["d"]
        results = body["results"] if isinstance(body, dict) else None
    except (KeyError, TypeError):
        results = None
    if not isinstance(results, list):
        raise SapError("the SAP response was not an OData v2 result list")
    following = body.get("__next")
    if following is not None:
        if not isinstance(following, str):
            raise SapError("the SAP next-page link was not a URL")
        if following.startswith("/"):
            root = urlsplit(base)
            following = f"{root.scheme}://{root.netloc}{following}"
        target, origin = urlsplit(following), urlsplit(base)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            raise SapError("the SAP next-page link pointed to a different host")
    return [r for r in results if isinstance(r, dict)], following


def _items(record: dict[str, Any], navigation: str) -> list[dict[str, Any]]:
    value = record.get(navigation)
    if isinstance(value, dict) and isinstance(value.get("results"), list):
        return [i for i in value["results"] if isinstance(i, dict)]
    return []


def pseudonym(secret: bytes, kind: str, value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return kind + "_" + hmac.new(secret, f"{kind}\0{text}".encode(), hashlib.sha256).hexdigest()[:16]


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def project(records: dict[str, list[dict[str, Any]]], secret: bytes, *, include_amounts: bool = False) -> dict[str, Any]:
    """OCEL 2.0 document for the extracted records. Deterministic for the same records and secret."""
    objects: dict[str, dict[str, Any]] = {}
    events: dict[str, dict[str, Any]] = {}
    skipped = 0

    def obj(kind: str, oid: str, attributes: dict[str, Any], relationships: Iterable[tuple[str, str]] = ()):
        item = objects.setdefault(oid, {"id": oid, "type": kind, "attributes": [], "relationships": []})
        for name, value in attributes.items():
            if value not in (None, "") and not any(a["name"] == name for a in item["attributes"]):
                item["attributes"].append({"name": name, "time": "1970-01-01T00:00:00Z", "value": str(value)})
        for target, qualifier in relationships:
            link = {"objectId": target, "qualifier": qualifier}
            if link not in item["relationships"]:
                item["relationships"].append(link)

    def event(activity: str, moment: datetime | None, precision: str, links: list[tuple[str, str]],
              user: str | None):
        nonlocal skipped
        if moment is None:
            skipped += 1
            return
        key = hashlib.sha256(json.dumps([activity, _iso(moment), sorted(links)]).encode()).hexdigest()[:24]
        attributes = [{"name": "time_precision", "value": precision}]
        if user:
            attributes.append({"name": "created_by", "value": user})
        events[key] = {"id": "ev_" + key, "type": activity, "time": _iso(moment), "attributes": attributes,
                       "relationships": [{"objectId": o, "qualifier": q} for o, q in links]}

    amounts = (lambda r: {"net_amount": r.get("TotalNetAmount"), "currency": r.get("TransactionCurrency")}) \
        if include_amounts else (lambda r: {})
    for r in records.get("sales_orders", []):
        oid = pseudonym(secret, "so", r.get("SalesOrder"))
        if not oid:
            skipped += 1
            continue
        obj("sales_order", oid, {"order_type": r.get("SalesOrderType"), "process_status": r.get("OverallSDProcessStatus"),
                                 **amounts(r)})
        items = sorted({p for p in (pseudonym(secret, "si", f"{r.get('SalesOrder')}/{i.get('SalesOrderItem')}")
                                    for i in _items(r, "to_Item") if i.get("SalesOrderItem")) if p})
        for item in items:
            obj("sales_order_item", item, {}, [(oid, "part_of")])
        event("Create sales order", parse_date(r.get("CreationDate")), "day",
              [(oid, "order")] + [(i, "item") for i in items], pseudonym(secret, "user", r.get("CreatedByUser")))
    for r in records.get("deliveries", []):
        oid = pseudonym(secret, "dl", r.get("DeliveryDocument"))
        if not oid:
            skipped += 1
            continue
        delivery_items = _items(r, "to_DeliveryDocumentItem")
        orders = sorted({p for p in (pseudonym(secret, "so", i.get("ReferenceSDDocument")) for i in delivery_items) if p})
        items = sorted({p for p in (pseudonym(secret, "si", f"{i.get('ReferenceSDDocument')}/{i.get('ReferenceSDDocumentItem')}")
                                    for i in delivery_items
                                    if i.get("ReferenceSDDocument") and i.get("ReferenceSDDocumentItem")) if p})
        for order in orders:
            obj("sales_order", order, {})
        for item in items:
            obj("sales_order_item", item, {})
        obj("delivery", oid, {"goods_movement_status": r.get("OverallGoodsMovementStatus")},
            [(o, "fulfils") for o in orders] + [(i, "carries") for i in items])
        links = [(oid, "delivery")] + [(o, "order") for o in orders] + [(i, "item") for i in items]
        user = pseudonym(secret, "user", r.get("CreatedByUser"))
        event("Create delivery", *with_time(parse_date(r.get("CreationDate")), r.get("CreationTime")), links, user)
        picked = parse_date(r.get("PickingDate"))
        if picked is not None:
            event("Pick items", *with_time(picked, r.get("PickingTime")), links, None)
        moved = parse_date(r.get("ActualGoodsMovementDate"))
        if moved is not None:
            event("Post goods issue", *with_time(moved, r.get("ActualGoodsMovementTime")), links, None)
    for r in records.get("billing_documents", []):
        oid = pseudonym(secret, "bd", r.get("BillingDocument"))
        if not oid:
            skipped += 1
            continue
        items = _items(r, "to_Item")
        deliveries = sorted({p for p in (pseudonym(secret, "dl", i.get("ReferenceSDDocument")) for i in items) if p})
        orders = sorted({p for p in (pseudonym(secret, "so", i.get("SalesDocument")) for i in items) if p})
        for d in deliveries:
            obj("delivery", d, {})
        for o in orders:
            obj("sales_order", o, {})
        obj("billing_document", oid, {"cancelled": str(bool(r.get("BillingDocumentIsCancelled"))).lower(),
                                      **amounts(r)},
            [(d, "bills") for d in deliveries] + [(o, "invoices") for o in orders])
        links = [(oid, "invoice")] + [(d, "delivery") for d in deliveries] + [(o, "order") for o in orders]
        user = pseudonym(secret, "user", r.get("CreatedByUser"))
        event("Create billing document", *with_time(parse_date(r.get("CreationDate")), r.get("CreationTime")), links, user)
        if r.get("BillingDocumentIsCancelled") is True:
            event("Cancel billing document", parse_date(r.get("LastChangeDateTime")), "second", links, None)
    ordered_events = sorted(events.values(), key=lambda e: (e["time"], e["id"]))
    return {"objectTypes": [{"name": n, "attributes": []} for n in sorted({o["type"] for o in objects.values()})],
            "eventTypes": [{"name": n, "attributes": []} for n in sorted({e["type"] for e in ordered_events})],
            "objects": sorted(objects.values(), key=lambda o: o["id"]), "events": ordered_events,
            "_skipped_records": skipped}


def extract(base: str, fetch: Callable[[str], Any], since: datetime, *, include_amounts: bool = False,
            max_records: int = MAX_RECORDS) -> dict[str, Any]:
    """Read every entity set changed since ``since``. ``fetch(url)`` returns parsed JSON or raises."""
    base = validate_base(base)
    records: dict[str, list[dict[str, Any]]] = {}
    pages = 0
    total = 0
    for entity in ENTITY_SETS:
        url: str | None = first_url(base, entity, since, include_amounts=include_amounts)
        records[entity.key] = []
        entity_pages = 0
        while url:
            entity_pages += 1
            pages += 1
            if entity_pages > MAX_PAGES:
                raise SapError("too many result pages; shorten the window")
            results, url = page(fetch(url), base)
            records[entity.key].extend(results)
            total += len(results)
            if total > max_records:
                raise SapError(f"more than {max_records} changed documents; shorten the window")
    return {"format": FORMAT, "records": records, "pages": pages,
            "counts": {k: len(v) for k, v in records.items()}}


__all__ = ["ENTITY_SETS", "FORMAT", "SapError", "extract", "first_url", "metadata_url", "page", "parse_date",
           "project", "pseudonym", "validate_base", "with_time"]
