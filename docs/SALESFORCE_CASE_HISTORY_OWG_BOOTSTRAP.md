# Salesforce CaseHistory — verified first-time OWG log bootstrap

Tracks #326 Section B, #264 Stage 3 and #370 R3. This fills the **manual first-import** gap in the existing reviewed Salesforce → OWG append workflow (SALESFORCE_CASE_HISTORY_OWG_APPLY.md). It is operator-controlled and local-only. It does not poll Salesforce, change Salesforce data, prove source completeness, certify a customer tenant or grant an agent business permissions.

## First run: prepare, inspect, import, verify, checkpoint

First run the existing Python module server.salesforce_case_history_incremental with the prepare subcommand, selected Salesforce instance, account namespace, 1–8 literal Case IDs, UTC window, private 0700 output directory, new CSV and manifest, and explicit read/export consent. It performs a bounded live Salesforce read. Inspect the private CSV, selected scope and event counts; keep the original manifest_sha256 from the prepare receipt. The source read is separate from this bootstrap command.

On the OWG computer, with a current **local dashboard session** and the exact chosen new log name:

~~~sh
export OWG_DASHBOARD_SESSION='<current-local-dashboard-session>'
python -m server.salesforce_case_history_owg_bootstrap \
  --checkpoint /private/owg-salesforce/checkpoint.json \
  --manifest /private/owg-salesforce/review-001.json \
  --confirm-manifest-sha256 '<64-hex-from-prepare>' \
  --log-name 'Reviewed Salesforce history' \
  --confirm-log-name 'Reviewed Salesforce history' \
  --port 8765 \
  --consent-prepared-read \
  --consent-local-import \
  --consent-checkpoint-advance
~~~

The command refuses any missing consent, changed CSV or manifest, stale checkpoint, non-bootstrap review, mismatched log-name confirmation, missing/revoked dashboard session or existing same-source namespace. It validates the exact private review digest, CSV row count, UTC window and hashed source IDs before writing.

It reads GET /v1/event-logs over literal 127.0.0.1 with the current OWG dashboard session and refuses a source namespace already present in the local log inventory. It then makes **one** authenticated, explicitly confirmed POST /v1/event-logs/import using the existing production CSV import API, mapping case_id, activity, timestamp and source_event_id. Both source and case namespace are sf_ plus the first 32 hex characters of the reviewed scope hash. No proxy, redirect, external host or provider write is allowed.

A successful import receipt must identify a **new** log ID, exactly the expected number of accepted events and zero rejected events. The command independently reads GET /v1/event-logs again and verifies that exact log ID, one exact source/case namespace, the same event count and zero rejected events. **Only after this independent readback** does the existing private CAS checkpoint commit run. A successful counts-only receipt includes the newly created opaque OWG log ID, accepted event count, owg_import_readback_verified: true and checkpoint_advanced: true.

Use the returned log ID for later reviewed windows with the Python module server.salesforce_case_history_owg_apply. Never create a second log for the same selected scope.

## Uncertain outcomes and safe recovery

If the POST may have succeeded but its response, independent readback or checkpoint commit fails, the CLI returns status: unverified, possible_local_import: true, checkpoint_advanced: false (exit code 3). **Do not automatically rerun bootstrap.** Inspect the local OWG log inventory and private checkpoint. If the selected source namespace already has a log, identify and confirm its exact log ID and run the existing reviewed Salesforce CaseHistory OWG apply command against the *same unchanged manifest*. Its source-ID deduplication safely verifies a previously imported batch before advancing the checkpoint. A missing checkpoint is not proof that the import failed. An interrupted .bootstrap.lock requires operator investigation before manual removal.

Before any POST, a refusal returns exit code 2 with no local import attempted. This operator flow cannot make the preflight scope check globally atomic across different computers or unrelated processes; do not run concurrent bootstrap commands for the same source. The existing local OWG authorization, storage consent, tenant boundaries, revocation and retention policy remain authoritative. Prepared files are private local artifacts that must be removed under the customer's retention policy. The CSV represents selected CaseHistory observations only; missing/late history beyond the bounded window remains unknown.

## Acceptance

The matching tests/test_salesforce_case_history_owg_bootstrap.py reuses the real disposable Salesforce TCP source and actual OWG FastAPI TCP/DuckDB fixture, testing first import, independent readback, durable checkpoint, missing consent, revocation, tampering, uncertain readback recovery using the production append path and duplicate-scope refusal. This is not real customer Salesforce acceptance. Before merge, require all applicable exact-head native Ubuntu/macOS/Windows pytest, browser, collaboration, accessibility, scale, starter and packaging/release checks, reviews and current-main mergeability.
