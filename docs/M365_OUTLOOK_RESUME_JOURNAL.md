# Microsoft Graph operator resume decision journal

This is a **local, explicit decision ledger**, not a Microsoft Graph synchronization engine. After completing a consented per-folder delta extraction, appending the CSV to OWG, and saving the verified append receipt, run the existing `server.m365_outlook_resume_gate` to review removals and target counts. The journal command repeats that gate's **live authenticated inventory read** immediately before recording a private, one-time decision.

Create an **existing owner-private directory** for the journal, separate from the provider export files. The private parent validation follows the same security policy as the Graph delta exporter. Set `OWG_DASHBOARD_SESSION` to the locally authorized dashboard session. Example:

```sh
python -m server.m365_outlook_resume_journal \
  --tenant reviewed-tenant --mailbox reviewed-mailbox --folder AQMkSelectedFolder \
  --grant-file /private/owg/grant.json \
  --candidate /private/owg/round-001.json \
  --csv-path /private/owg/round-001.csv \
  --receipt-file /private/owg/round-001-receipt.json \
  --journal-dir /private/owg/resume-journal \
  --successor-label round-002 \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --consent-local-read --confirm-removals-reviewed
```

The result records the source CSV digest, exact target log, event count verified by count, operator-selected successor label, and the explicit fact that **no provider requests or checkpoint advances occurred**. The same candidate and destination cannot be recorded with a different successor label. Exact repeated decisions return `idempotent_replay=true` after fresh authorization; a corrupted or conflicting record fails closed. Records are created exclusively with owner-only permissions and synced to disk. A failed/partial write is deliberately left for operator inspection rather than overwritten. Keep these records under your organization's approved retention policy; no automatic deletion is performed.

## Acceptance and limits

This provides durable local replay prevention for **operator decisions**, not a cryptographic proof of imported event membership, a guarantee of no provider replay, or an automatically committed cursor. A previously copied or modified record is not an independent source of trust: each call rechecks the production resume gate, including the original private candidate, grant, CSV SHA-256, receipt, explicit removal review and current authenticated OWG inventory. The successor label is an operator-chosen identifier, not a Graph delta token. Never put tokens or PII in it. The actual provider cursor remains unchanged; the operator must still explicitly use the existing Graph extraction CLI with `--confirm-previous-import` after reviewing the decision. Do not interpret this as real Microsoft sandbox acceptance.

Tests exercise owner-private persistence, repeat verification, conflicting successor refusal, revoked gate refusal and corrupt-record refusal. Required native cross-platform, browser, process collaboration, scale, accessibility, starter and release CI must pass before merging.
