# Microsoft 365 / Outlook: explicitly selected Graph metadata → OWG CSV

This is an operator-invoked, read-only, bounded Microsoft Graph connector for one to eight previously selected Outlook message immutable IDs. It creates a private CSV accepted by OWG Event logs → Import CSV and manually triggered append/deduplication. It does not read email subjects, sender/recipient addresses, bodies, attachments, conversation text, or hidden folders. It is not a complete mailbox synchronization service.

## Authorization and setup

Obtain explicit mailbox-owner/tenant-administrator authorization for a delegated Microsoft Graph read token with minimum permitted Mail.ReadBasic access to the selected messages. Verify permissions and immutable-ID support in the actual customer tenant; the local scope label is not proof of Microsoft tenant identity. Use the Microsoft Graph immutable-ID preference when obtaining selected IDs; default Graph IDs may change when messages move. Even immutable IDs may change across certain mailbox migrations.

Create an owner-only grant file in an owner-only output directory (POSIX chmod 700 directory, chmod 600 file). This operator-managed opt-in marker is not an identity-provider permission or substitute for token revocation:

    {
      "version": 1,
      "enabled": true,
      "consented": true,
      "tenant_scope": "reviewed-tenant",
      "mailbox_scope": "reviewed-mailbox",
      "message_ids": ["AAMkSelectedImmutableMessageId"]
    }

Set OWG_M365_GRAPH_TOKEN in your secret manager/environment, never on the command line or in the grant. From the repository root:

    python -m server.m365_outlook_selected --tenant reviewed-tenant --mailbox reviewed-mailbox --message-id AAMkSelectedImmutableMessageId --grant-file /private/owg/m365-grant.json --output /private/owg/outlook-events.csv --consent-selected-read --consent-local-export

The output path must not exist; no file is overwritten. A successful run creates a 0600 CSV with case_id, activity, timestamp, source_event_id. IDs are SHA-256 scoped hashes. Activity is Observed read status: read/unread, not an assertion that the modification timestamp is when the message was read. Timestamp is the Graph last-modified instant normalized to UTC; it may reflect other changes. Stable source_event_id combines selected message ID, modification timestamp and observed read state, so identical replays deduplicate when the operator appends to the same OWG source namespace. A new observation is a snapshot, not necessarily an email workflow event.

## Bounded behavior and privacy

For each of at most eight selected IDs, the connector makes one exact read-only HTTPS GET to the Microsoft Graph me/messages selected-ID endpoint, requesting only id, receivedDateTime, lastModifiedDateTime and isRead. It sends the ImmutableId preference, uses a 15-second timeout, TLS hostname validation, no ambient proxy and no redirect following. The response must match the selected ID and approved fields; missing/foreign IDs, non-200 responses, unexpected URLs, malformed/duplicate-key JSON, non-UTF-8 content, oversized responses and partial batches fail closed. Maximum accepted response is 16 KiB per message. No partial CSV is published.

The local grant is checked before every GET, after every response and immediately before create-only publication. Revocation stops subsequent checked operations, but cannot atomically stop an in-flight request or undo previously exported data. Revoke the actual Microsoft Graph token or provider ACL to enforce provider-side denial. Output remains local business metadata subject to retention, deletion, backup and access policies.

The stdout receipt contains only counts and explicit limitations, not message IDs, mailbox addresses, credentials, URLs, subjects or private text. The connector performs no provider writes, implicit OWG import, checkpoint advance, unattended polling or agent actions. It does not verify Microsoft tenant identity, historical completeness, missing/deleted messages, all folder coverage, token rotation, pagination or source freshness. The selected me mailbox is determined by the actual delegated token; mailbox_scope is a human-declared label, not independently verified.

## Acceptance and next work

Committed tests exercise actual request construction, consent/grant, exact response binding, private atomic publication, stable replay keys, later revisions, revocation and refusal scenarios with disposable Graph-shaped HTTP transport. They do not contact a real Microsoft customer or sandbox tenant. Before merge, all six exact-head GitHub Actions workflows and required native Ubuntu/macOS/Windows pytest, browser, collaboration, scale, accessibility, starter and packaging/release jobs must pass, with reviews resolved and conflict-free main.

For production readiness under #326 and #370 R3: independently test with an authorized Graph sandbox, confirm delegated permissions and immutable IDs, implement tenant/mailbox identity verification, managed token renewal/revocation, Graph delta pagination and change/deletion handling, rate limits/backoff, durable checkpoints and source completeness/freshness receipts. A successful CSV export is not full Outlook sync or verified business outcomes.
