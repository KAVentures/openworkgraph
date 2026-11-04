# M365 uncertain-append offline event identity comparison

This operator CLI advances #326 / #370 R3 without performing a retry. It consumes the **private** `review-*.json` manifest produced by `server.m365_outlook_attempt_event_review` and a separately acquired owner-private CSV with a `source_event_id` column. The CSV must have been explicitly exported from the intended OWG event log by the operator; the CLI **does not authenticate the export's origin**. It compares exact source-event IDs, reports missing identities and duplicate exported IDs, and writes an exclusive owner-only `comparison-*.json` file. It performs zero network requests.

Run from the repository root:

```sh
python -m server.m365_outlook_event_membership_compare \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER \
  --grant-file /private/grant.json --review-file /private/review-123.json \
  --export-file /private/export.csv --output /private/comparison-123.json \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --consent-local-read --consent-private-export
```

Use a private owner-only directory and files; no raw IDs are printed to stdout. The grant is checked before and after local reads. Missing consent, malformed or ambiguous CSV, changed scope, invalid source identities, occupied destination and unsafe permissions fail closed. Comparison is bounded to 100,000 export rows / 16 MiB and 200 candidate identities.

**Important:** a CSV can be incomplete, stale, filtered, edited or from the wrong log. A match does not prove authenticated event membership; a missing identity does not prove safe retry. The output always has `event_membership_verified=false`, `safe_to_retry=false`, `checkpoint_advanced=false`. It must not be used to clear an attempt latch, append to OWG, or commit a Graph cursor. A future authenticated, scoped per-event OWG readback is required for definitive reconciliation. No Microsoft Graph sandbox or customer acceptance is claimed.
