# Microsoft Graph per-folder delta → private OWG event CSV and review cursor

This is a **bounded, operator-invoked, read-only** continuation of the selected Outlook connector. Unlike [selected-message export](M365_OUTLOOK_SELECTED.md), it uses Microsoft Graph's real per-folder messages/delta endpoint and follows the nextLink until the complete round returns a deltaLink. It is not a background sync, provider-identity proof or a complete multi-folder mailbox mirror.

Microsoft documents delta as **per folder**, with select/top on the initial request and opaque continuation URLs thereafter: [Microsoft Graph delta messages](https://learn.microsoft.com/en-us/graph/delta-query-messages). A single initial round may contain the entire folder, so the hard budgets below intentionally refuse large folders instead of presenting a truncated round as complete.

## Authorization and explicit scope

Obtain actual mailbox-owner/tenant authorization and a delegated Graph token with least Mail.ReadBasic scope; verify this scope and immutable IDs against a real authorized tenant. Tenant and mailbox scopes are **local operator labels**, not verified Microsoft tenant/mailbox claims. A folder ID is required, and the exact folder grant is checked before and after every Graph request and before each private artifact is created. An owner-private (0600) grant in a 0700 directory:

    {
      "version": 1,
      "enabled": true,
      "consented": true,
      "tenant_scope": "reviewed-tenant",
      "mailbox_scope": "reviewed-mailbox",
      "folder_id": "AQMkSelectedImmutableFolderId"
    }

Set OWG_M365_GRAPH_TOKEN through a local secret manager/environment, never as a CLI argument or in the grant. Run from the repository root:

    python -m server.m365_outlook_delta \
      --tenant reviewed-tenant --mailbox reviewed-mailbox \
      --folder-id AQMkSelectedImmutableFolderId \
      --grant-file /private/owg/delta-grant.json \
      --output /private/owg/round-001.csv \
      --candidate /private/owg/round-001.json \
      --consent-delta-read --consent-local-export

The output CSV is compatible with OWG's explicitly invoked **Event logs → Import CSV** (case_id, activity, timestamp, source_event_id), with hashed folder-scoped message/case/revision identities. Each row is an **observed metadata snapshot**, not proof of a user read action or message business outcome. The Graph modification timestamp may reflect any message change. No subject, body, address, attachment or raw message ID is retained.

The 0600 candidate JSON contains the scope digest, CSV filename and SHA-256, completed deltaLink, counts and hashed removed_case_ids. **Treat it as a sensitive cursor credential** and restrict its access, backups and retention. A removal marker means the item was removed from this folder's delta scope, **not proof of permanent mailbox deletion**. These hashes are for explicit retention review only; no OWG evidence is deleted. A counts-only stdout receipt reports the number of removals without revealing the identities or cursor.

## Next round — only after confirmed import

Review the complete CSV, explicitly import/append it to the correct OWG source namespace, verify the local result, and separately approve using the candidate. Then run:

    python -m server.m365_outlook_delta \
      --tenant reviewed-tenant --mailbox reviewed-mailbox \
      --folder-id AQMkSelectedImmutableFolderId \
      --grant-file /private/owg/delta-grant.json \
      --output /private/owg/round-002.csv \
      --candidate /private/owg/round-002.json \
      --resume-candidate /private/owg/round-001.json \
      --confirm-previous-import \
      --consent-delta-read --consent-local-export

The previous candidate and exact CSV digest must still match the current grant's source scope. The CLI never advances a checkpoint, imports or appends automatically; the previous-import flag is an **operator assertion**, not independently verified OWG receipt. Do not use the next cursor before actually importing and verifying the preceding batch. Empty delta rounds create a header-only CSV and candidate and must still be reviewed.

## Bounds, privacy and recovery

Each round is limited to **eight HTTPS GET pages, 25 records per page and 200 total source records**. The reader uses only graph.microsoft.com over HTTPS, exact selected folder path, a 15-second timeout, the ImmutableId preference, no ambient proxies and no redirects. It validates allowed cursor parameters, duplicate keys, JSON envelope and selected fields, response size, grant currency and replay/contradictory observations. Missing, expired, rate-limited or unauthorized responses refuse; no partial delta round is published and no cursor is advanced. Large folders, 429 responses and expired cursors require operator review rather than silent retries. The selected folder may not be a complete view of the mailbox; moved or deleted records need separate policy decisions.

A completed round creates the private CSV first and its SHA-bound candidate second. If the second local write fails, a CSV **may remain without a usable candidate**; inspect it and choose fresh paths rather than treating that round as committed. Both the current output directory and the directory containing any previous candidate must be owner-only. Unsafe directories are refused before the next provider request; the delta CLI reports a generic refusal rather than exposing local paths or source identities. Files are create-only and never overwritten. Revocation prevents future checked operations but cannot retroactively erase an existing CSV or interrupt an already-in-flight network request atomically. Actual Graph credential/ACL revocation is separate from the local grant.

Integration tests use disposable Graph-shaped HTTPS responses through the **production request/parser/export path** and cover two-page completion, real continuation URL validation, explicit removal review, next-round cursor binding, consent, revocation, replay, changed source scope, unsafe redirects, partial/over-budget pages and private output. They do **not** establish Microsoft customer/sandbox acceptance. All exact-head native Ubuntu/macOS/Windows pytest, browser, process collaboration, scale, accessibility, starter and packaging/release checks remain mandatory before merge.

**Still required for production lifecycle:** authorized Graph sandbox acceptance, verified delegated tenant/mailbox identity, token renewal/revocation, full-folder and multi-folder completeness, safe 429 retry scheduling, retention/deletion approval, authenticated OWG import receipts and durable, verified automatic checkpoint commits. Do not label this as a complete managed connector.


### Expiring scoped grants

A private, explicitly consented folder grant may include an optional
`"expires_at": "2026-12-01T12:00:00Z"` field (UTC, seconds precision).
The production delta grant validator refuses malformed or expired timestamps,
even when `enabled` and `consented` are true. Every adapter that delegates
to this validator inherits the check; revocation by setting `enabled: false`
still takes precedence. Existing six-field grants remain supported for
backward compatibility and **do not expire automatically**. Operators should
issue expiring grants and explicitly revoke old grants when migrating.
Expiry is a local authorization gate, not a Microsoft OAuth refresh or
provider-side revocation guarantee; provider token expiry and actual tenant
acceptance still require independent validation.
