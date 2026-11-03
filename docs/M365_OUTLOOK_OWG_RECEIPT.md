# Microsoft Graph delta → authenticated OWG append receipt

This is a **manual, one-shot local import** after a complete Graph per-folder delta export. It consumes the existing owner-private CSV and SHA-bound candidate from `server.m365_outlook_delta`; it does not contact Microsoft Graph, retrieve messages, delete evidence, commit a delta cursor, or poll. It uses the authenticated local OWG REST append endpoint and independently reads the log inventory before and after the POST.

## Prerequisites

Create or explicitly import a selected OWG CSV log under source namespace `m365_<first 32 hex characters of scope_sha256>` and the same case namespace. The existing log must have the exact source identity; the operator must confirm its `log_...` ID. Use a current owner-private Graph folder grant and original, unmodified candidate and CSV in the same private directory. The source labels in the grant are **not** independent Microsoft tenant/mailbox identity verification.

With a locally authorized dashboard session in `OWG_DASHBOARD_SESSION`, run:

```sh
python -m server.m365_outlook_owg_receipt \
  --tenant reviewed-tenant --mailbox reviewed-mailbox --folder AQMkSelectedFolder \
  --grant-file /private/owg/delta-grant.json \
  --candidate /private/owg/round-001.json \
  --csv-path /private/owg/round-001.csv \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --port 8765 --consent-local-read --consent-local-append
```

A successful counts-only JSON response includes the CSV SHA-256, target log ID, batch, added/skipped counts and independently read event totals. This is a **local OWG append verification**, not provider-state or source-history completeness proof. The candidate remains unchanged: the operator must separately review the import receipt before choosing to resume the Graph delta cursor. The older `--confirm-previous-import` flag remains an operator assertion, not a cryptographic receipt requirement.

The tool checks grant scope, file privacy, candidate schema, CSV digest, row count and source IDs before any POST. A zero-event round can be verified without a POST. An unsafe source, mismatched target or revoked grant refuses. **Any failure after attempting the POST is uncertain**: a transaction may have committed; inspect OWG append history and inventory, and never blindly rerun. No automatic retry or provider write occurs.

## Limits and acceptance

At most 200 Graph-derived rows and a 2 MiB private CSV are admitted. This command uses literal loopback, dashboard session authorization, storage-confirmation header and no ambient proxy. It does not create the initial log, write an on-disk receipt, verify individual imported event IDs independently, or automatically advance a checkpoint. Local inventory counts plus server append result are useful but not a cryptographic independent event-level audit. Cross-process concurrent appends may invalidate the count check, resulting in an uncertain status. No source deletion is authorized.

Tests cover actual candidate validation and HTTP request generation with disposable loopback transport, explicit consent, target isolation, revocation, digest changes and uncertain post-readback. Full native Ubuntu/macOS/Windows, browser, process collaboration, scale, accessibility, starter and packaging/release CI is mandatory before merge. Real Microsoft Graph tenant acceptance, managed credentials and retention/deletion approval remain separate.
