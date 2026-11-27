# BigQuery approved warehouse event feeds

Refs #1572, #326 and #370 R3/R20. Expands the generic enterprise SQL source capability with a native BigQuery Standard SQL API adapter. The connector uses the existing retained event store, checkpoint transaction, scheduler, Gateway jobs, publication and reviewed private crosswalks.

## Customer deployment

Create an approved BigQuery view exposing stable immutable STRING event and case IDs, STRING activity codes, TIMESTAMP instants and optionally STRING resource IDs. Only explicitly reviewed non-sensitive activity labels are retained. The administrator must verify the view and its dependencies; in particular, do not approve a view with remote functions, external side effects or unreviewed sources. The fixed adapter SELECT is not proof of the behavior of an arbitrary customer-defined view.

Grant a dedicated service account only the BigQuery job creation permission in the billing project and the necessary data read permissions on the approved view. Prefer an authorized view and narrowly scoped IAM. The service-account OAuth scope is bigquery.readonly; there is no delegated user identity or domain-wide delegation. This slice supports a deployment-controlled RSA key, not workload identity federation or automatic key rotation. Follow organizational key policies; if key creation is prohibited, do not enable this adapter.

Configure the deployment-owned OWG_BIGQUERY_CATALOG JSON:

    {"orders":{"billing_project":"fixture-billing","data_project":"fixture-data","dataset":"approved","view":"events","location":"EU","service_account":"owg-reader@fixture-data.iam.gserviceaccount.com","key_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","credential_env":"OWG_BIGQUERY_KEY_ORDERS","event_column":"event_id","case_column":"case_id","activity_column":"activity","timestamp_column":"event_at","resource_column":"resource","activity_labels":{"OPEN":"Order opened","CLOSE":"Order closed"},"maximum_bytes_billed":104857600}}

Supply the referenced key as standard base64 of unencrypted PKCS#8 DER RSA 2048–4096-bit bytes, using the existing restricted environment or owner-only managed secret-file mechanism. Protect it as a service credential. Never paste it into the dashboard or support ticket. Only a public-key fingerprint-derived binding is persisted. Replacing the key, location, project, view, mapping or billing cap requires reviewed reconfiguration.

The billing project is distinct from the data project. Set maximum_bytes_billed deliberately: the API applies this cap to each submitted query, not an aggregate daily or monthly budget. Under on-demand billing the limit controls bytes billed; it is not a universal monetary spending cap (capacity pricing, other project activity and other costs are outside it). Set appropriate project quotas and cost controls separately.

## Operator workflow and query lifecycle

Open Enterprise connectors and choose the approved BigQuery view. Review its exact digest, data relation, billing project, geographic location, byte limit, retained labels and resource handling. Configure initial time, incremental window, overlap, lag and result page size. Approve billable reads, OAuth authentication and retention. Saving or refreshing inventory makes no provider request.

Each authorized run obtains a new short-lived OAuth access token from the fixed Google token endpoint using a signed RS256 service-account JWT with a short assertion lifetime and the read-only scope. The token is not persisted. A fixed SESSION_USER() query checks the service account, including when the event window is empty. The event query includes the principal in every row for a second readback. This is not a complete IAM privilege assessment.

The data query is fully qualified and uses named TIMESTAMP window parameters; it returns epoch microseconds and fixed scalar aliases. No user-authored SQL, DDL, DML, export or arbitrary destination is accepted. Query caching is disabled and the provider byte cap and best-effort job timeout are sent on each query. A single query result is paginated via a validated job ID, exact billing project and location and opaque page token. Pending jobs are polled within the shared request/time budget. A failed or timed-out run does not imply the remote job was cancelled; administrators should inspect their BigQuery job history if necessary. No remote cancellation or spend refund is claimed.

The adapter checks projected schema, scalar cells, exact totalRows, repeated page tokens, job identity, provider errors, principal, timestamp bounds, approved labels and unique event IDs. It refuses over-budget results without advancing a checkpoint. Billed bytes are recorded only when returned by the provider; unknown is not zero. All requests share page/byte/time limits. Reduce the window or adjust the page size if necessary; a large provider result is not silently truncated.

## Retention and cross-source linkage

Event, case and resource IDs are profile-scoped HMAC pseudonyms. The existing retained engine atomically appends events and advances the checkpoint and deduplicates replayed IDs. The source is not CDC; source mutations, deletions and late events outside overlap are not automatically reconciled. A complete returned result does not prove complete historical coverage.

Use the existing local private crosswalk preparation with the exact native case string and no native_type. The canonical retained case identity is independently checked against the selected published snapshot. Only pseudonymous reviewed match tags leave the source host. Same native ID across unrelated views does not establish common business identity.

## Acceptance

Synthetic transport tests use actual RSA signature verification and cover OAuth scope, query cost cap, job polling, exact pagination, malformed schemas and counts, identity, replay, empty windows, credential drift and a retained native crosswalk. Live customer IAM, view correctness, query cost, remote timeout and production performance still require customer sandbox acceptance.

Google API references:
- https://developers.google.com/identity/protocols/oauth2/service-account
- https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/query
- https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/jobs/getQueryResults
- https://docs.cloud.google.com/bigquery/docs/paging-results
