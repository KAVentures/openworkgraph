# Snowflake approved warehouse event feeds

Refs #1572, #326 and #370 R3/R20. This adapter adds Snowflake SQL API event ingestion to the common retained enterprise connector runtime. It is not a general SQL executor or a Snowflake change-data-capture implementation.

## Deployment

A warehouse administrator must create a curated view whose event IDs, case IDs and activity codes are non-null VARCHAR values, with an optional VARCHAR resource column and a TIMESTAMP_LTZ or TIMESTAMP_TZ event instant. IDs must be stable and unique within the approved source, and timestamps must be representable at microsecond precision. Do not supply customer names, message text, passwords or sensitive values as activity codes. Define reviewed labels explicitly.

Use a dedicated service user, role-restricted programmatic access token (PAT), least-privilege role with SELECT on the view, warehouse USAGE and necessary catalog/schema USAGE, and an appropriate network policy. Ensure the user has no additional effective write or unrelated source privileges. The adapter verifies the current account locator, user and primary role, not the complete privilege set. No automatic PAT creation, refresh or rotation is implemented.

Configure the deployment-owned OWG_SNOWFLAKE_CATALOG JSON mapping, e.g.:

    {"orders":{"host":"fixture-account.snowflakecomputing.com","warehouse":"INGEST","database":"BUSINESS","schema_name":"APPROVED","view":"ORDER_EVENTS","role":"OWG_READ_ONLY","expected_account":"FIXTURE","expected_user":"SERVICE_USER","credential_env":"OWG_SNOWFLAKE_TOKEN_ORDERS","event_column":"EVENT_ID","case_column":"CASE_ID","activity_column":"ACTIVITY","timestamp_column":"EVENT_AT","resource_column":"RESOURCE","activity_labels":{"OPEN":"Order opened","CLOSE":"Order closed"}}}

Names are exact, case-sensitive simple SQL identifiers. Only the organization-account public hostname form is supported in this slice. Set the referenced token in the existing environment or approved private-file secret mechanism. Never paste the PAT into the dashboard. The source definition and credential are pinned; changing either requires reviewed reconfiguration.

Open Enterprise connectors, select the approved Snowflake view, review its digest, role and labels, choose first timestamp, window, overlap and reporting lag, and approve reads and metadata retention. Saving or refreshing inventory makes no provider request. Run once or schedule via the existing retained-feed controls. Gateway jobs and governed snapshot publication use the common wire contract; private native crosswalk and reviewed in-place rotation are not claimed.

## Read and retention contract

The adapter issues fixed SELECT statements only through the Snowflake SQL API v2. The account locator, user and primary role are checked before and after the event query. Event boundaries are bound nanosecond epoch integers, never concatenated user SQL. The result schema is checked on the actual data statement, including zero-row results. Native LTZ/TZ encoding is converted to exact UTC microseconds; naive or submicrosecond instants are refused rather than silently rounded.

The original result manifest provides row counts and partition metadata. Each additional partition is requested from a locally constructed same-host path using the validated statement handle and partition index, never an externally supplied URL. Incomplete results, duplicate event IDs, unknown labels, unexpected schemas or out-of-window timestamps abort the entire retention attempt. Statements have bounded server execution timeout and requests have byte, row, page and time limits. Pending results and uncertain transport failures are not automatically resubmitted. If necessary, reduce the configured window. Rate-limit receipts use the existing cooldown.

Event/case/resource IDs are pseudonymized with the profile key; raw source identifiers and token values are not stored in retained event rows. The common transactional import/append engine commits the checkpoint with the retained event identities and deduplicates replayed rows. A completed window is not proof of historical completeness or deletion handling. No automatic cross-source joins are implied by matching native IDs.

## Verification

Synthetic HTTP fixtures exercise partitioned result validation, malformed handles and manifests, partial reads, cooldown, timestamp precision, invalid source definitions, consent, credential drift, duplicate IDs and overlapping replay. The real source permissions, provider-specific empty-result responses, server timeout and cancellation behavior, token lifecycle and performance require live customer sandbox validation. No real Snowflake account is contacted by the tests.

API reference reviewed 2026-11-27:

- https://docs.snowflake.com/en/developer-guide/sql-api/reference
- https://docs.snowflake.com/en/developer-guide/sql-api/submitting-requests
- https://docs.snowflake.com/en/developer-guide/sql-api/handling-responses
- https://docs.snowflake.com/en/user-guide/programmatic-access-tokens
