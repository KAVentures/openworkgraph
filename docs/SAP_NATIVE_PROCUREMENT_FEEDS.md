# Native SAP procurement evidence (OData V2)

Part of enterprise delivery #1572. Extends the common retained-feed,
Gateway job and approved snapshot publication stack #1573–#1580.
The existing SAP order-to-cash OCEL connector is unchanged.

## Supported sources

| Native service | Entity | Evidence and time precision |
|---|---|---|
| API_PURCHASEORDER_PROCESS_SRV | A_PurchaseOrder | CreationDate (day) |
| API_MATERIAL_DOCUMENT_SRV | A_MaterialDocumentHeader with to_MaterialDocumentItem | PostingDate (day), selected purchase order reference and GoodsMovementType |
| API_SUPPLIERINVOICE_PROCESS_SRV | A_SupplierInvoice with to_SuplrInvcItemPurOrdRef | PostingDate (day), selected purchase order reference |

This is a purchase-order-centric, bounded business-date evidence feed, not
an accounting ledger, financial reconciliation, SAP CDC feed, or full
procure-to-pay lifecycle. SAP marks the purchase order OData V2 API as
deprecated in some releases; this adapter is for systems that still expose
the reviewed V2 contract. V4 migration requires separate validation.

All three streams use one profile-local pseudonymous purchase order case.
Material movement and invoice events are one per matching native document
line, with a composite fiscal/material year, document and line identity.
Purchase order header creation events use the order number. No supplier,
employee, material description, quantities, amounts, currency, financial
balances or payment information is requested or retained. The movement type
is a source code, not an inferred business outcome. Invoice posting date
on a reference is not proof of invoice approval, settlement or payment.
No payment, requisition approval or invoice approval event is fabricated.

## Deployment source catalog

A deployment administrator supplies an exact source catalog through
OWG_SAP_P2P_CATALOG. The following is an illustrative schema, not usable
credentials or metadata hashes:

    {
      "production_mm": {
        "service_root": "https://sap.customer.example/sap/opu/odata/sap",
        "sap_client": "100",
        "auth_type": "bearer",
        "credential_env": "OWG_SAP_P2P_PRODUCTION",
        "metadata_sha256": {
          "purchase_orders": "<reviewed SHA-256>",
          "material_movements": "<reviewed SHA-256>",
          "supplier_invoices": "<reviewed SHA-256>"
        }
      }
    }

Use the actual customer HTTPS origin and client. For each enabled service,
retrieve its authenticated GET /$metadata using the same client and
credential, review the entity, date-field type, keys, expansion properties
and supported query options, then hash the exact response bytes with SHA-256.
Do not place provider credentials in URLs or the catalog. Inject a bearer
token or Basic user:password credential into the named deployment environment
variable. Basic credentials are sent only over TLS to the reviewed root.
The adapter has no automated SAP OAuth refresh; credential replacement
requires a new profile and an explicit review. Provider ACLs must restrict
the account to the required read operations. Source host, client, credential
reference and metadata digests are part of the immutable reviewed catalog
definition. Any change in that definition stops existing profiles.

## Operator workflow

In Enterprise connector operations choose the native SAP procurement feed.
Select an available approved source, one to 100 exact numeric purchase order
numbers (normalized to ten digits), and one or more enabled streams. Review
the source definition digest and approve day precision and retention.

The purchase order query restricts records by exact purchase order number.
Material and invoice header queries are bounded by date, not selected PO
numbers at the provider. They may return unrelated references transiently.
A separate explicit approval is required to enable either of those streams;
only selected purchase-order references are retained. The deployment account
should restrict read visibility as far as practical. Do not select these
streams if transient receipt of unrelated references is unacceptable.

The initial UTC midnight is within 365 days. Advancing windows cover one to
seven complete days, with optional one-day overlap and configurable lag.
Dates are represented at midnight UTC for storage and explicitly labeled
as day events. Neither the order nor elapsed time of events within a day can
be established. Do not interpret zero-duration cases, tied timestamps or
within-day sequence as actual timing. This is a declared date-only adapter,
not timezone conversion of SAP transaction timestamps.

Saving does not contact SAP. Run an explicitly approved bounded read, set
a recurring local job, or register an approved Gateway job. Exact snapshot
publication is a separate consent step. Source credentials remain on the
source host.

## Ingestion and safeguards

- Fixed GET paths and exact OData V2 projections, filters and stable ordering.
  TLS validation, no redirects or environment proxy. sap-client is fixed.
- Metadata byte digests verified before each service and after the entire
  window. A metadata change fails closed, including harmless byte-level
  changes; require review and a new source profile.
- Require server inline count and complete bounded pagination. $top is a
  total result limit, so it is not sent. The configured maximum page size
  is a local acceptance bound, not a request for provider page sizing.
- Only same-origin exact entity continuations with a bounded skip or
  skiptoken are accepted. Original filters, projection, expansion and client
  are reconstructed on each continuation. Query mutation, repeated cursor,
  changed total count, unordered or duplicate headers, mismatched parent
  keys, malformed business dates and incomplete expanded reference
  collections fail the entire window.
- All services share the common 20-request/10,000-record/bounded-byte/read
  deadline limits, including metadata requests. Unselected references
  count toward the read budget. A large server page or deep expanded
  collection can exceed limits; no implicit narrowing or truncation occurs.
- Atomic retention and checkpoint commits, exact source-key replay
  deduplication, bounded cooldown and credential/profile invalidation
  reuse the common runtime. An empty complete window can advance without
  creating a fabricated log.

## Validation boundaries

The service shapes are based on SAP's native API references. Runtime exact
metadata hashes establish reviewed bytes, not independent proof of API
semantics, account identity or complete history. The tests use injected
OData response fixtures and actual retained workspace transactions, with
dashboard setup covered by Chromium fixtures. Full CI and actual SAP
sandbox acceptance remain required. Verify real metadata, UTC day encoding,
count/pagination/expansion support, stable key representations, permissions
and release compatibility. Some SAP examples expose date offsets; this
strict adapter rejects non-midnight or offset representations rather than
guessing a timezone. Backdated postings and corrections outside the overlap,
deleted records, incomplete provider visibility and historic changes may be
missed. There is no transactional snapshot across the three SAP services.

References:
- https://help.sap.com/docs/PRODUCT_ID/91af7f8d3acd47da90d33aaacfcd0d59/acd2da57df6cc525e10000000a4450e5.html
- https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/eb2a39dd0c124edabffe8b2d55e1/8bb0d08295044ee3af444b4f2a6e4457.html
- https://help.sap.com/docs/r/bb9f1469daf04bd894ab2167f8132a1a/LATEST/en-US/7bc52558ef790a02e10000000a44147b.html
