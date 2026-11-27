# Central connector event integration

Part of #1572. This is a separate, optional disclosure path over the
customer-hosted connector jobs introduced in #1577. It does not silently
turn operational receipts into raw event-log sharing. The local retained
source log remains canonical. The Gateway stores a bounded, short-lived
projected snapshot for explicit cross-source review and mining import.

## Prepare, approve, publish

1. On the dedicated source host, stop competing workers and select the
   local profile and event-time window. Review the retained log locally.
   Preparation requires a current enabled profile, credential binding,
   retained log and completed checkpoint; no provider call is made.

       python -m server.enterprise_connector_snapshot prepare \
         --profile rest_... --since 2026-11-25T00:00:00Z \
         --until 2026-11-26T00:00:00Z --out /private/snapshot.json

   The file is created exclusively with 0600 permissions. It contains at
   most 5,000 events (not a truncated subset). CLI output contains the
   manifest only, including the exact profile pin, event and case counts,
   event-time bounds, retained checkpoint, activity mode and row digest.
   The source event and case IDs are re-pseudonymized using the profile
   key. Activity names are coded by default. No actor, resource, source
   attributes, original provider IDs or source tokens are included.
   Coded activities remain stable within a profile, not across profiles.
   Timestamps and pseudonyms are potentially identifying, not anonymous.
   Readable activity labels require --labels --confirm-label-disclosure;
   review labels locally because they may contain sensitive text.

2. A Gateway owner opens **Enterprise connector jobs → Separately approved
   connector event integration**, selects the registered exact job, and
   pastes the manifest envelope (not the event file). The owner reviews the
   range, counts, source revision, activity mode and digest, explicitly
   approves disclosure, and selects 1–168 hours of retention. Readable
   labels require separate approval. This is a new grant, not job-dispatch
   consent. One active grant/snapshot per job; twenty per organization.

3. The source host uses the dedicated job worker token and the same
   Gateway/organization/job environment as the existing connector worker:

       python -m server.enterprise_connector_snapshot publish \
         --snapshot /private/snapshot.json --grant cep_... --confirm-disclosure

   The CLI rebuilds the exact local projection before uploading. A changed
   log, checkpoint, profile or deleted source requires fresh review.
   A preflight checks the exact organization, job, grant and manifest digest
   before transmitting any event payload. The Gateway repeats authorization
   in its database transaction, validates every row, ordering, count and
   digest, and accepts only exact idempotent replays. A changed job revision
   invalidates a pending upload. A completed publication remains readable
   by the Gateway owner until explicit removal or expiration; deleting
   the source does not retroactively revoke a separate central copy.

4. The owner selects published snapshots, reviews their exact manifest
   pins, and downloads either JSON (with provenance) or a machine-import CSV
   (no spreadsheet escaping, because exact labels are needed for mining).
   The CSV is **not safe for opening in spreadsheet software** if readable
   activity labels were approved; use JSON for review or import the CSV
   directly into the event-log workspace. Supply the following mapping:

       event_id_column: event_id
       case_column: case_id
       activity_column: activity
       timestamp_column: observed_at
       source_namespace_column: source_namespace

   Source namespace is the exact Gateway job ID. The existing supplied-key
   importer keeps separate case namespaces, validates every row and
   deduplicates replayed exports. No case linkage between provider
   profiles is inferred. Coded activities are profile-specific; they do not
   establish shared activity semantics either. Use existing explicit case
   mapping/record linking tools only when separately justified.
   Maximum ten exact snapshots and 20,000 total events per export.

## Security, expiry and recovery

- Existing Gateway instance-owner authority is reused. Ordinary admins,
  viewers, employee tokens and evidence integrations do not receive
  publication or export permissions. Worker tokens can upload only to
  their own current grant and cannot download event rows. The bootstrap
  owner token retains existing bootstrap behavior; disable bootstrap API
  access in deployed environments per existing Gateway documentation.
- Every mutation uses the existing organization-serialized transaction.
  Grant IDs are random and never reused, including when approving the
  same manifest after removal. Export and delete require exact grant and
  revision pins; uploads require current job revision and digest.
- Expiration is enforced in every read and upload, even if background
  cleanup fails. A bounded cleanup sweep runs at startup and every minute.
  The UI reports cleanup failures. Physical DB rows are deleted; this
  does not certify secure erasure from PostgreSQL WAL, replicas, backups
  or already downloaded/imported copies. Source and central copies each
  have separate retention. Remove stale local snapshot files securely.
- The Gateway never reads provider endpoints in these operations. No
  raw provider payload, source credential, resource identifier, hidden
  text or source attributes are accepted by the snapshot contract. Audit
  entries record grant IDs, counts and mode, not event labels or rows.
- Snapshot content is worker-supplied and independently checked for
  consistency, not for truth or provider completeness. The local
  checkpoint is a source change-window boundary, not a guarantee of
  completeness for the selected event-time range. Nothing in this
  workflow grants execution authority or establishes a business outcome.

## Validation

Tests cover actual retained-log imports, two independent source profiles,
central disclosure, combined reimport with exact source keys and duplicate
replay, explicit label consent, incorrect worker and grant, tampered
manifest, expiry, delete/reapproval, stale job revision, owner-only HTTP
authorization and local artifact privacy. The existing disposable
PostgreSQL CI service runs the same transactional tests in addition to
SQLite.

Remaining: automatic central publication with separately reviewed rolling
retention, central scalable mining over authorized source snapshots,
cross-source semantic mappings, large-volume storage, live multi-host
validation and measured throughput. This increment is a bounded integration
and export workbench, not an enterprise data warehouse.
