# Salesforce CaseHistory: reviewed local OWG append with verified checkpoint

Scope: [#326 B](https://github.com/KAVentures/openworkgraph/issues/326), [#264 Stage 3](https://github.com/KAVentures/openworkgraph/issues/264), [#370 R3](https://github.com/KAVentures/openworkgraph/issues/370). This is an **operator-invoked**, local-only second phase for the existing [incremental Salesforce CaseHistory prepare](SALESFORCE_CASE_HISTORY_INCREMENTAL.md). It replaces the unverifiable `commit --confirm-imported` attestation with an actual authenticated OWG append response and independent append-history readback. It is **not** automatic polling, Salesforce provisioning, a remote tenant service, source completeness proof, or a customer sandbox certification.

## Initialize the selected OWG event log once

The current OWG API cannot append to a nonexistent log. On the **first** reviewed Salesforce CSV, import it manually into **Large event logs → Import CSV** on the same OWG computer. Select `case_id`, `activity`, `timestamp`, and **supplied source event ID** `source_event_id`. Set both the source namespace and case namespace to `sf_` followed by the **first 32 hexadecimal characters of `scope_sha256`** from `prepare`. This namespace is bound to the selected instance, account namespace and exact set of Case IDs; changing that selection requires a separately reviewed target, never an implicit join. Confirm local storage in the dashboard. Record the returned `log_<16hex>` ID. The first `apply` may then append the same CSV again: source-event-ID deduplication will report it as duplicates, not add another copy. An existing log with another source namespace, multiple namespaces or another case namespace is refused. This conservative policy prevents mixing unrelated sources.

Subsequent prepared windows can be appended directly to that existing log, with no manual CSV import. Source IDs are stable hashes of caller-declared Salesforce account and literal history IDs; neither OWG nor this workflow independently authenticates their business identity. Source identity conflicts reject the entire OWG append transaction. This command does not read Salesforce again and performs no Salesforce writes.

## Review → apply

Run the existing `python -m server.salesforce_case_history_incremental prepare ...` with explicit Salesforce read/export consent, a private 0700 directory, a new CSV and manifest, a bounded window and 1–168 hour overlap. Independently review the CSV, scope and counts. Keep the original `manifest_sha256` from its counts-only output.

With a **current local OWG dashboard session** (not an AI bearer token), confirm the exact existing log ID and run:

```sh
export OWG_DASHBOARD_SESSION='<current-local-dashboard-session>'
python -m server.salesforce_case_history_owg_apply \
  --checkpoint /private/owg-salesforce/checkpoint.json \
  --manifest /private/owg-salesforce/review-002.json \
  --confirm-manifest-sha256 '<64-hex-from-prepare>' \
  --log-id log_0123456789abcdef \
  --confirm-log-id log_0123456789abcdef \
  --port 8765 \
  --consent-prepared-read --consent-local-append --consent-checkpoint-advance
```

The command validates the private manifest and CSV against the original digest, event count, hashed source IDs, window, current checkpoint and selected scope **before any write**. It contacts only `http://127.0.0.1:<port>` with a current `OWG-Session` header, disabled proxies and redirects. It first checks the target log and its one exact source/case namespace using the existing authenticated `GET /v1/event-logs`. It then uses the existing `POST /v1/event-logs/{id}/append` with explicit local storage confirmation, `source_event_id` mapping and bounded reviewed CSV bytes. The production OWG transaction performs source-ID deduplication and rejects conflicts atomically. The command checks returned added/duplicate/rejected counts and source identity, then separately reads `GET /v1/event-logs/{id}/appends` to verify the exact recorded batch and counts. **Only after both succeed** does it call the existing digest/CAS-protected atomic checkpoint commit.

A successful response includes `owg_append_readback_verified: true`, `checkpoint_advanced: true`, batch and counts. It contains no credentials, source IDs, raw case values or local file paths. The source remains historically incomplete/unknown; no agent or business action is authorized by these records.

## Uncertain outcomes and operational boundaries

If local append returns an error, times out, is revoked mid-operation, produces a malformed response, or the subsequent history readback/checkpoint commit fails, the command reports **`status: unverified` and `possible_local_append: true`**, with no checkpoint advancement by this command. **Inspect the local OWG append history and current checkpoint before an operator-controlled retry.** A successful but unacknowledged append may have committed. Retrying the exact unchanged CSV is safe from duplicate events because the existing source-event-ID index deduplicates exact keys; it may still create another append-history batch. Never automatically retry arbitrary provider or local writes. If the review is stale or the checkpoint already advanced, prepare a new review. A `.apply.lock` file indicates an interrupted apply: investigate before manually clearing it; never silently bypass it.

The command does not solve source-side deletion, token refresh, centralized revocation/retention, late records beyond overlap, source pagination over 200 rows, full case completeness, or unattended scheduling. The local dashboard session is authoritative for local append authorization. A compromised local administrator can still forge their own artifacts or confirmation; hashes protect accidental drift, not malicious local operators. Delete prepared CSVs/manifests under the organization's retention policy. If the prepared window is empty, use the separate reviewed empty-window checkpoint command, **not** this append command.

## Acceptance and release gate

`tests/test_salesforce_case_history_owg_apply.py` exercises a real disposable Salesforce-shaped TCP source, production `prepare`, actual FastAPI OWG routes over loopback TCP and actual local DuckDB source-ID append/storage, history readback, checkpoint advancement, failed readback followed by deduplicated retry, revocation, wrong target, tampering and missing consent. These tests do not certify a real Salesforce tenant or customer deployment. Before merge, require exact-head native Ubuntu/macOS/Windows pytest, SCIM where applicable, browser, process collaboration, scale, accessibility, starter and packaging/release checks, resolved reviews and current-main conflict-free mergeability. No CI or security gates may be bypassed.
