# Hosted browser work-text relay (experimental; personal accounts only)

This is a **separate, OFF-by-default** channel added after PR #181.
It does **not** add text to canonical structural evidence, the ordinary
Gateway sync batches, agent-session messages, or model instructions.

## Consent and flow

The dashboard's **Privacy** page requires three independent user choices:

1. **Capture visible work text for AI** (OFF on install)
2. **Allow local AI apps to read saved work text** (OFF on install)
3. **Allow connected cloud AI apps to retrieve captured work text** (OFF on install)

Master AI access must also be enabled, the recorder must be running, and the
device must be linked to the same *personal* OAuth account as the hosted MCP.
The desktop only polls for requests when these permissions allow it. The third
switch is not available until the first two have been granted. Disabling either
parent permission also disables the third. The ordinary API/MCP bearer
**cannot** turn these settings on; a real dashboard session is required.

The flow is: ChatGPT calls `search_work_text(query)` → Gateway stores only an
encrypted short-lived request → linked desktop polls outgoing HTTPS → local
OWG searches its own *redacted* SQLite work-text store → device returns up to
four bounded matching excerpts → Gateway encrypts the response at rest →
ChatGPT calls `search_work_text(request_id=...)` to see previews and
`get_work_text_excerpt(request_id,index)` to read a chosen excerpt. The
response remains subject to the hosted redactor and prompt-injection filter.

If the device is offline, the tool returns `pending` then expires, rather than
pretending to have text. The local worker polls at most once per 15 seconds
**while cloud access is enabled**. This can add up to ~5,760 lightweight
requests per day per enabled device; costs should be monitored before scaling.

## No standing cloud document corpus

- Queries and excerpts are **readable only for 75 seconds**. SQL ciphertext
  is physically removed at the next relay operation, or by the operator's
  scheduled purge job. If every client goes offline and cleanup is not
  scheduled, expired ciphertext can remain in database storage and backups.
- Result encryption uses Fernet (`cryptography`), with a shared operator-owned
  key. There is no fallback to plaintext when the key is absent.
- Neither SQL rows nor audit entries contain readable text. Audit records store
  request IDs and counts, not query text or excerpt bodies.
- Each request is bound to a particular signed-in OAuth actor and a single
  most-recently linked device. A different user/device cannot answer or read it.
- Browser content is already locally redacted and screened before storage;
  another hosted redaction pass and prompt-injection boundary run before MCP
  results are returned. Automated filtering cannot guarantee that every secret
  or sensitive detail is detected.
- Revoking cloud access triggers deletion of that device's pending and answered
  relay rows at the next outgoing sync iteration. If the device is offline,
  its rows become unreadable after 75 seconds but may remain encrypted at rest
  until the next relay request or scheduled purge. Revocation cannot
  erase information already supplied to a model/conversation.
- The existing **Never record** host/title exclusions are applied before local
  content is stored and again during local retrieval.
- Enterprise/workspace organizations are denied by default. A separate
  administrator-authorized policy must be designed before enabling them.

## Operator setup, NOT automatic

The new code does **not** deploy itself and does not configure production
secrets or create Vercel preview deployments. After review and merge, the
operator must configure the **same high-entropy Fernet key** as a protected
server-side variable named `OWG_WORK_TEXT_RELAY_KEY` in both the Gateway
deployment and the personal OpenWorkGraph OAuth MCP plugin deployment.
Generate one key securely, for example with `Fernet.generate_key()`, and
never put it in Git, logs, browser storage, a QR code, or the device.
Both services must point to the same Gateway database. Without the key, text
retrieval stays unavailable and all other OWG functionality works normally.
Rotate the key only after outstanding 75-second requests have expired.

**Required before production:** schedule `python -m gateway.work_text_relay`
against the shared Gateway database at least once per minute to physically
purge expired relay rows even when all devices and model clients are offline.
This cleanup does not need the encryption key. Verify the job is running and
consider SQL backup/WAL retention separately. Without this maintenance,
the 75-second expiry is an application-read boundary, not a guarantee of
physical deletion.

The desktop worker already uses its existing paired device credential.
The hosted plugin uses the current OAuth identity to resolve that person's
`oauth-sub:...` organization/actor ID and never trusts model-supplied identity.

## Explicit current limitations

- **No native desktop text capture.** Only browser text already recorded after
  explicit opt-in is searchable.
- **No strong resource linkage yet.** Results have a local row reference,
  hostname, redacted page title, timestamp, and text type (page or draft), but
  not necessarily the exact email/document ID. Do not infer missing references.
- **Lexical search only.** Query words must occur in the stored redacted title
  or body. No semantic retrieval, embeddings, or automatic task inference.
- **No bulk export or offline cloud-history access.** That requires a distinct
  explicit consent and reviewed encrypted storage architecture.
- **Live real-browser tests and production cross-deployment testing are not
  substituted by passing unit/CI tests.** Keep feature disabled for sensitive
  work until independent security assessment and synthetic live tests succeed.

See `server/work_text_capture.py`, `gateway/work_text_relay.py`,
`connector/work_text_relay.py`, and `gateway/public_plugin_mcp.py`.
