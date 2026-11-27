# Oracle Fusion Cloud procurement event feeds

Refs #1572, #326 and #370 R3/R20. This adapter directly reads selected purchase requisition and purchase-order headers through Oracle Fusion Cloud Procurement REST resources. It does not require a custom record producer, but does not constitute a complete procure-to-pay history extractor.

## Configure

Use a dedicated integration user permitted to read the selected business units and header resources. Restrict its actual privileges independently. This slice uses HTTPS Basic authentication for deployments permitting it, with a deployment-owned Oracle Fusion host and an existing protected password reference. OAuth-only deployments, independent authenticated principal readback and in-place credential rotation are not supported. The adapter sends only GET requests.

Set OWG_ORACLE_FUSION_CATALOG through the deployment configuration:

    {"procurement":{"host":"fixture.fa.us2.oraclecloud.com","integration_user":"owg_readonly","credential_env":"OWG_ORACLE_FUSION_PASSWORD_PROC","requisition_business_units":[1001],"purchase_order_business_units":[1002]}}

The password must be supplied through the existing restricted environment or private-file secret mechanism. Do not put credentials in the dashboard or support logs. Source definitions and password bindings are pinned; changing either requires reviewed reconfiguration.

Open Enterprise connectors and choose the approved source. Select exact requisition and purchase-order header IDs and approved business units. The API accepts header IDs and business-unit IDs as decimal strings to preserve 64-bit precision in browser JavaScript. Up to nine headers per resource and ten total are supported by the common twenty-request budget. Choose timestamp facts, initial UTC timestamp, window, overlap and reporting lag. Approve both reads, snapshot semantics and retention. Saving or refreshing inventory makes no provider request.

## Source evidence semantics

The adapter uses the fixed versioned collections purchaseRequisitions and purchaseOrders. It selects only the documented header key, business-unit key, CreationDate, and the optional relevant date fields: requisition SubmissionDate, purchase order OrderDate (supplier communication), and LastUpdateDate. No descriptions, supplier identities, amounts, comments, employee names or status fields are requested. Creation date is also needed to validate later dates. Exactly one returned record, matching both header and business unit, is required. Missing, inaccessible or ambiguous records fail the entire window rather than being interpreted as deletions.

Submission, communication and latest update fields are mutable snapshot observations. They are not verified approval actions, transition histories or evidence of every edit. A nullable milestone produces no event. A latest update equal to creation does not produce a separate update event. Observed timestamps must be exact timezone-qualified instants with at most microsecond precision, and must not precede creation. Timestamp-specific identities deduplicate the same observation on replay; changed date observations may coexist with earlier observations. A requisition and an order are separate case notions even when their numeric IDs match. No cross-system or requisition-to-order join is inferred.

Each selected header is read twice; the complete selected timestamp facts must agree before retention. Both reads share the common page, byte and time limits. No external link or redirect is followed. Agreement does not establish a transactional source snapshot, historical completeness or deletion handling. The common retained event-log engine atomically appends events and advances the checkpoint, deduplicating overlaps. Header and case identities are HMAC pseudonyms within retained events; exact reviewed IDs remain in the local profile configuration. Recurring jobs, passive inventory and governed publication use the shared enterprise provider contract.

## Acceptance

Synthetic HTTP tests cover two-resource ingestion, exact IDs above the JavaScript safe integer range, business-unit and header mismatch, null dates, repeated reads, changed dates, consent, credential drift, malformed response refusal and replay. Live tenant access, query semantics, authorization, timestamps and performance require customer sandbox acceptance. This implementation is intentionally selected-header evidence, not account-wide discovery or complete historical audit.

Oracle documentation:

- https://docs.oracle.com/en/cloud/saas/procurement/26a/fapra/op-purchaseorders-get.html
- https://docs.oracle.com/en/cloud/saas/procurement/26d/fapra/op-purchaserequisitions-get.html
