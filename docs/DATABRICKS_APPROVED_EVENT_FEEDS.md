# Databricks approved warehouse event views

Parent #1572 (warehouse expansion), #326 and #370 R3/R20. This adapter extends the consolidated enterprise connector stack (#1589). It does not create a second event store.

## Deploy and review

The customer DBA creates a curated Unity Catalog view with stable unique STRING event IDs, STRING case IDs, STRING activity codes, TIMESTAMP (not TIMESTAMP_NTZ) event instants and optionally STRING resource IDs. Only grant the configured service principal permission to use the specific SQL warehouse and select the reviewed view and its necessary catalog/schema. Do not grant writes. No source deletion reconciliation or CDC is inferred from snapshots.

Configure OWG_DATABRICKS_CATALOG as a deployment-owned JSON mapping, e.g.:

    {"orders":{"host":"fixture.cloud.databricks.com","warehouse_id":"0123456789abcdef","expected_principal":"service-principal-id","credential_env":"OWG_DATABRICKS_TOKEN_ORDERS","catalog":"business","schema_name":"approved","view":"order_events","event_column":"event_id","case_column":"case_id","activity_column":"activity","timestamp_column":"event_at","resource_column":"resource","activity_labels":{"OPEN":"Order opened","CLOSE":"Order closed"}}}

Set the referenced bearer credential via the existing environment or private-file delivery. Use the organization's approved Databricks service identity; a token change requires explicit reconfiguration (in-place rotation is not claimed). The adapter checks current_user() against the configured principal before and after the read. The workspace hostname and catalog hash are pinned. The check is not independent proof of all grants or the view's business correctness.

Open the local Enterprise connectors page, choose the approved Databricks view, inspect its exact revision and retained labels, choose first timestamp, window, overlap and reporting lag, and explicitly approve configuration, source reads and retention. Saving performs no network request. Run once or schedule via the existing retained feed controls. Central Gateway job scheduling and governed snapshot publication use the shared provider contract; [Private native crosswalk](GATEWAY_PRIVATE_CROSSWALKS.md) accepts exact approved-view case strings; reviewed rotation remains unsupported.

## Data path

Only fixed SELECT statements against the catalog-approved view, a zero-row schema probe and current_user() checks are issued. All identifier components must be simple reviewed SQL names. Window boundaries are named BIGINT microsecond parameters; the interval is half-open. Results use the stable v1 Statement Execution API, JSON_ARRAY/INLINE, cancel-on-wait-timeout. Chunk paths are derived from validated statement IDs and indices, not supplied links. External result links, redirects, incomplete/truncated results, schema mismatch, unknown activity codes, duplicate identities, out-of-window timestamps and over-budget reads fail closed without advancing checkpoints.

The existing retained-feed transaction commits source-key deduplicated event rows and checkpoint together. Event, case and resource identities are HMAC pseudonyms scoped to the profile; raw values are not stored. Only reviewed activity labels are retained. The API's complete returned window is not proof of complete history, late-arriving rows outside overlap, deleted source data or actual business outcomes. Use a smaller window if a read exceeds the 10,000-event, request, byte or time budget.

## Validation

Synthetic transport tests cover exact case populations, overlapping retries, multiple result chunks, schema/principal mismatches, malformed responses, credential and deployment revision drift, injection-shaped identifiers and unknown activities. Actual workspace, warehouse permissions, cloud-specific networking and production performance still require customer sandbox acceptance.

Primary API documentation: https://docs.databricks.com/api/statement-execution/v1/execute-statement and https://docs.databricks.com/aws/en/dev-tools/sql-execution-tutorial .
