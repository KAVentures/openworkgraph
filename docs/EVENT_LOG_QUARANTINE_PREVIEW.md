# R6 — Preview quarantined CSV rows before release

The existing local **Event logs → Analyse → Data quality → Rejected rows kept for review** panel now requires a read-only preview before its **Release rows** action. This applies only to rejected CSV rows explicitly retained at import or append. It does not turn on retention for earlier logs.

## Operator workflow

1. Import/append a CSV with **keep rejected rows for review** explicitly selected. The local session retains only the bounded mapped rejected rows, not arbitrary source files.
2. Open the selected log's **Data quality** panel and enter the **UTC offset to assume** for timestamps lacking one (for example `+01:00`). The offset is an explicit assumption, not a verified source time zone.
3. Click **Preview release**. The panel displays **rows considered**, **rows parseable with the chosen offset**, and **rows still rejected**. Nothing is appended, discarded, or modified by preview. Rows already present in the event log may be skipped as duplicates during actual release; **parseable rows are not a promise of events added**.
4. Review the counts and deliberately click **Release rows**. This uses the existing append path, which records a new quality report and a decision. Editing the offset, switching logs, or discarding rows invalidates the dashboard's preview. If another local operation changed the selected rejected rows, the server refuses the stale release and requires a new preview.

The existing **Discard kept rows** action remains a separate explicit destructive decision. Legacy direct API clients may still use release without a preview token for backward compatibility; the dashboard always supplies one.

## Session-authenticated API

`POST /v1/event-logs/{log_id}/quarantine/preview` accepts JSON:

```json
{"naive_timestamp_offset":"+01:00","batch":0}
```

`batch` is optional; when omitted, all retained rejected batches are previewed. The endpoint requires the same local dashboard session as the quarantine and quality endpoints, refuses bearer-only API tokens, and returns `Cache-Control: no-store`. It is read-only and returns counts only, with an `expected_preview_sha256` snapshot fingerprint. It does **not** return rejected row values, source identifiers, resource names, samples, or new permissions.

The existing `POST /v1/event-logs/{log_id}/quarantine/release` accepts an **optional** `expected_preview_sha256` alongside the offset and optional batch. When provided, the server recomputes the fingerprint while holding the workspace lock and rejects a changed selection before appending any events. The token binds the selected log, batch, offset and retained row contents. It is an optimistic concurrency check, **not** an authentication credential, source attestation or authorization to release; the local session and explicit release action are still required.

The preview and release share the same CSV parsing predicate, including the required case/activity and timestamp validity checks. A release can still add fewer events than the preview's eligible count because of append deduplication. Other data defects, upstream completeness, time-zone correctness and real provider/customer acceptance are **not** established.

## Verification

The Python regression covers preview nonmutation, deterministic snapshots, changed offsets, stale selection refusal, batch isolation, invalid input and session-authenticated HTTP. The Node regression extracts and exercises the **actual dashboard quarantine block**, covering explicit preview before release, offset invalidation, changed-log refusal and discard invalidation. Required native Ubuntu/macOS/Windows pytest, Node/browser, process collaboration, accessibility, scale, starter and packaging/release workflows must execute successfully on the exact PR head before merge. Synthetic fixtures are not real customer/provider acceptance.
