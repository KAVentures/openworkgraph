# Salesforce CaseHistory — bounded query-cursor pagination

Tracks #326 Section B, #264 Stage 3 and #370 R3.

## What changed

The existing selected-case Salesforce live reader and reviewed incremental **prepare** command can now follow the real Salesforce REST Query nextRecordsUrl cursor, rather than refusing every multi-page query. This is a bounded, explicit operator opt-in: the default remains **one GET / one page**, and --max-pages must be deliberately raised to 2–8. Each request is a read-only authenticated GET with the same approved bearer and exactly the originally confirmed Salesforce origin. No proxy, redirect, provider write, hidden background sync, or source text upload is added.

The reader accepts only the opaque, same-origin relative cursor form /services/data/v60.0/query/<opaque-id>. It refuses a full URL, query parameters, fragments, unexpected paths, repeated cursors, missing or inconsistent totalSize, malformed responses, source records outside the selected case/time window, duplicate history IDs, out-of-order records, and more than 200 total source rows. It validates every page before producing any private CSV. **All pages must finish with done=true and the exact expected record count**; an incomplete result never becomes a reviewed checkpoint.

Example live export with two permitted pages:

~~~sh
export OWG_SALESFORCE_READ_TOKEN='<approved-read-only-bearer>'
python -m server.salesforce_case_history_live \
  --instance https://example.my.salesforce.com \
  --confirm-host example.my.salesforce.com \
  --namespace approved-sandbox \
  --case 500000000000001 \
  --since 2026-10-01T00:00:00Z \
  --until 2026-10-02T00:00:00Z \
  --output /private/owg-salesforce/history.csv \
  --max-pages 2 \
  --consent-selected-read --consent-private-export
~~~

The owner-only output directory must already exist and the CSV path must be new. The counts-only receipt includes pages_fetched; source IDs, raw subject/text, credentials and case identifiers are never included in the receipt. The projected CSV contains only hashed case and event IDs, selected creation/status activities, and timestamps. It can be imported using OWG's existing CSV event-log parser.

For a repeatable reviewed incremental window, use the **existing** server.salesforce_case_history_incremental prepare command and add --max-pages 2 (or another explicitly bounded 1–8 value). Its manifest and checkpoint formats remain unchanged. The new pages_fetched count appears in the prepare receipt, not the durable checkpoint. After human review, use the existing verified owg_apply path for a previously initialized log or the separately reviewed first-import bootstrap when available. A successful source read alone never advances the checkpoint.

## Failure and acceptance boundaries

If a query exceeds 200 rows or eight pages, the reader refuses. Shorten the selected UTC window or reduce selected cases; never silently truncate. A 201st row is deliberately refused even when Salesforce reports a complete response. Salesforce row visibility, permissions, retention, late events and deleted records can still make a technically complete paginated query incomplete as a business source; source_completeness remains unknown. This feature is **not** a Salesforce sandbox certification or a deletion feed, and it does not replace managed credential/revocation or unattended scheduling.

The new tests/test_salesforce_case_history_pagination.py exercises real loopback TCP Salesforce-shaped two-page responses, bearer propagation, production OWG CSV parsing, incremental prepare with digest-bound private artifacts and no checkpoint advance, and fail-closed cursor, consent, count, ordering, revocation and redirect cases. Local loopback HTTP is a test-only option; production requires the exact confirmed Salesforce HTTPS host. Merge requires the exact PR head's native Ubuntu/macOS/Windows pytest, browser, process-collaboration, accessibility, scale, starter and packaging/release workflows, and resolved reviews. Real authorized provider acceptance is a separate gate.
