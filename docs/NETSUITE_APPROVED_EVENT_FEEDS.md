# NetSuite curated SuiteQL event records

Refs #1572, #326 and #370 R3/R20. This adapter adds renewable certificate-authenticated native NetSuite SuiteQL reads to the retained enterprise connector runtime. It consumes an explicitly curated customer custom record, not arbitrary SuiteQL or a general native transaction-audit extractor.

## Customer deployment

Create an approved custom event record in the customer's NetSuite account with stable internal record IDs, immutable case identities, reviewed activity codes, an exact integer UTC epoch-microsecond field and optionally a resource identifier. The customer owns the event producer, UTC conversion and business-case definitions; validate them independently. IDs and event semantics must be immutable across overlapping windows. Do not send sensitive record values or free-form text as activity codes. The custom record must be available to SuiteQL under the dedicated least-privilege integration role.

Configure NetSuite OAuth 2.0 machine-to-machine client credentials: an integration record, an approved entity/role mapping and a certificate registered to that mapping. Grant only the REST web services scope and required read permissions; do not grant writes. The adapter verifies successful authentication to the fixed account-specific host with the reviewed client and certificate, not an independent role readback or complete privilege set. Customer administrators must validate actual privileges.

Configure the deployment-owned OWG_NETSUITE_CATALOG JSON mapping, e.g.:

    {"orders":{"account_domain":"123456-sb1","client_id":"fixture-client","certificate_id":"fixture-certificate","credential_env":"OWG_NETSUITE_KEY_ORDERS","event_record":"customrecord_owg_events","case_column":"custrecord_case","activity_column":"custrecord_activity","epoch_micros_column":"custrecord_micros","resource_column":"custrecord_resource","activity_labels":{"OPEN":"Order opened","CLOSE":"Order closed"}}}

Use the account-domain identifier rather than the URL (sandbox domains use hyphens). The referenced credential is standard-base64-encoded unencrypted RSA 2048–4096-bit PKCS#8 DER private-key bytes; store it through the existing restricted environment or owner-only private-file mechanism. Protect it like any service credential. Never paste the key into the dashboard or a support ticket. Only a public-key fingerprint-derived binding is persisted. Replacing the key or source definition requires reviewed reconfiguration; in-place certificate rotation is not implemented.

Open Enterprise connectors, select the approved record, inspect its exact revision, account and retained labels, configure the first timestamp, window, overlap, lag and page size, and approve authentication, two complete bounded reads and retention. Saving or refreshing inventory makes no provider request. Each authorized run requests a new short-lived OAuth access token with a signed PS256 assertion (120-second lifetime, unique assertion ID, fixed audience and only rest_webservices scope). The token is not persisted. Recurring jobs therefore do not depend on an operator manually replacing hourly access tokens. Central jobs and governed snapshot publication use the existing shared provider contract. Private native crosswalk is not implemented.

## Exact read and retention semantics

The adapter issues a fixed SELECT with validated custom-record identifiers and integer microsecond window boundaries. No arbitrary SQL is accepted. The query has deterministic order by epoch and internal ID. The REST SuiteQL endpoint uses Prefer: transient and explicit limit/offset parameters. The adapter validates count, totalResults, hasMore, offset, ordering, unique event IDs and the returned field population on every page. It never follows response links. Resource is omitted entirely if not approved.

The same complete window is read twice; projected results must match exactly. This detects many concurrent edits and page shifts, but offset pagination does not establish a transactional snapshot or provider completeness. The common request/time/byte/event budgets apply to both scans and the grant. For example, with 500 rows per page, the 20-request budget permits at most nine pages per scan (4,500 events per window) before other constraints. Reduce window size for larger volumes. An incomplete or inconsistent scan aborts without advancing the checkpoint. Rate-limit receipts use the existing cooldown.

Event/case/resource IDs are HMAC pseudonyms scoped to the retained profile. Only reviewed activity labels and UTC timestamps are retained. The existing transactional import/append engine commits the checkpoint with event identities and deduplicates overlaps. Source deletions, later mutations and events arriving outside the overlap are not reconciled automatically.

## Verification

Synthetic HTTP fixtures use real RSA-PSS signature verification and cover fresh token grants per run, page population and order, double-scan disagreement, exact timestamps, rejected scope, credential drift, duplicate IDs, empty windows, and retained replay. Live NetSuite customer sandbox acceptance, native custom-record field serialization, service-role scope, token lifecycle and performance are outstanding.

Official documentation:

- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_162730264820.html
- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_162790605110.html
- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_162755359851.html
- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_157909186990.html
- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_156414087576.html
