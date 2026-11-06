# SAP S/4HANA order to cash

OWG can read order-to-cash documents from SAP S/4HANA through SAP's standard OData
v2 APIs into one local, object-centric event log (OCEL 2.0). It is read-only, off
until you configure it, and each read needs your confirmation.

## What is read

| Service | Entity set | Fields selected |
|---|---|---|
| `API_SALES_ORDER_SRV` | `A_SalesOrder` with `to_Item` | order, type, creation date, last change, process status, creating user, item numbers |
| `API_OUTBOUND_DELIVERY_SRV;v=0002` | `A_OutbDeliveryHeader` with `to_DeliveryDocumentItem` | delivery, creation, picking and goods-issue date and time, last change, goods-movement status, creating user, referenced order and item |
| `API_BILLING_DOCUMENT_SRV` | `A_BillingDocument` with `to_Item` | billing document, creation date and time, last change, cancelled flag, creating user, referenced delivery and order |

Net amounts and currencies are added only when you tick **Include net amounts**.
Customer (sold-to and ship-to) fields are never selected.

## The log

**Object types**

| Type | Relationships |
|---|---|
| `sales_order` | — |
| `sales_order_item` | `part_of` its order |
| `delivery` | `fulfils` its orders; `carries` its items |
| `billing_document` | `bills` its deliveries; `invoices` its orders |

**Events**

| Event | Time precision | Linked to |
|---|---|---|
| Create sales order | day; SAP gives a date only | the order and its items |
| Create delivery | second | the delivery, its orders and items |
| Pick items | second | the delivery, its orders and items |
| Post goods issue | second | the delivery, its orders and items |
| Create billing document | second | the billing document, its deliveries and orders |
| Cancel billing document | second (the last-change time) | the billing document, its deliveries and orders |

Every event carries a `time_precision` attribute.

**Pseudonyms.** Document numbers and user names are replaced by keyed pseudonyms:
HMAC-SHA256 with a key kept in the source settings file. The same document always
gets the same pseudonym on this installation, and no document number is stored.
Creating users appear only as a pseudonymous `created_by` event attribute.

To use the built-in [order-to-cash pack](DOMAIN_PACKS.md), map:

| Pack role | Log name |
|---|---|
| order | `sales_order` |
| item | `sales_order_item` |
| delivery | `delivery` |
| invoice | `billing_document` |
| create order | Create sales order |
| pick item | Pick items |
| ship | Post goods issue |
| create invoice | Create billing document |

The pack also requires payments. Customer payments live in SAP's accounting
(clearing) data, which this source does not read yet, so the pack's payment
metrics need another payment source.

## Reads, checkpoints and failures

- **Window.** The first read covers the chosen number of days, 30 by default.
  Each later read covers everything changed since the last checkpoint minus the
  overlap, 24 hours by default and at most 168, which catches late changes.
- **Paging.** SAP's server-driven paging is followed (`__next`) only on the same
  host.
- **Limits.** 500 rows per page, at most 200 pages per entity set and 50,000
  changed documents per read. A larger backlog is refused; shorten the window.
- **Duplicates.** Events are appended with the event log's duplicate skipping, so
  re-reading the overlap never adds an event twice.
- **Checkpoint.** It moves forward only after the log write succeeded. Any
  failure leaves it unchanged, so a retry loses nothing.
- **Rate limits.** HTTP 429 and 502–504 are retried up to four times, honouring
  `Retry-After` up to 60 seconds. Connection errors are retried the same way.
- **Credentials.** HTTP 401 or 403 stops the read as
  `grant_expired_or_insufficient`. The credential is read from the environment
  variable you name (it must start with `OWG_SAP_`). Bearer tokens and Basic
  `user:password` are supported, and the credential is never stored.
- **Schema changes.** Each service's `$metadata` is fingerprinted on every read.
  The read report lists services whose metadata changed since the previous read.
- **Deleted documents.** SAP's standard APIs do not report physically deleted
  documents, so deletions are not detected. A cancelled billing document is an
  event.
- **Disconnect.** Disconnecting removes the configuration, checkpoint and
  pseudonym key. The event log stays until you delete it.

## Routes

All routes need the local dashboard session.

| Route | Purpose |
|---|---|
| `GET /v1/sources/sap-o2c` | Status, checkpoint, last read |
| `PUT /v1/sources/sap-o2c` | Configure: `service_root`, `auth_type`, `credential_env`, `overlap_hours`, `initial_days`, `include_amounts` |
| `POST /v1/sources/sap-o2c/run` | One read; needs `X-OWG-Source-Read-Confirmed: true` |
| `POST /v1/sources/sap-o2c/disconnect` | Disconnect |

## Not yet proven

The request shapes follow SAP's published API specifications and are tested
against a synthetic OData service: paging, retries, grant errors, schema changes
and overlap. They have not been run against a real SAP tenant. Before relying on
them, check the field names against your system's `$metadata`, and run a first
read with a short window.
