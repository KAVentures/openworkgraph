# Optional cloud work-text synchronization (experimental)

This feature is **OFF by default** and must not be enabled publicly until
the privacy notice, terms, vendor agreements, and security review are complete.
It is a separate content data plane; normal structural events are unchanged.

## User choices

Privacy controls remain separate:
1. Capture visible browser work text locally (OFF by default).
2. Allow local AI to read it (OFF by default).
3. **Share redacted work content with my cloud AI apps** (OFF by default).

Enabling the third setting requires a checked, specific acknowledgement that
content leaves the computer, could include third-party information, and must
be shared only with authority. The local server independently requires the
current `cloud-work-text-v1` acknowledgement version and records its own
timestamp. A normal MCP/API token cannot activate the setting.

**Only text captured AFTER cloud opt-in** is synchronized. OWG does not
retroactively upload prior local snapshots, native desktop app contents,
clipboard contents, full web documents, or ordinary structural activity history.

## How it works

- Existing linked-device Gateway HTTPS client authenticates with its
  existing device credential. No new relay, polling queue, user-managed
  encryption key, or separate identity system.
- The worker checks local consent, master AI permission, recorder state,
  Pause/Stop, and the Never record lists before each upload. It sends at
  most 50 *redacted and bounded* page/draft snapshots per request, and a
  list of local references to reconcile deletions/exclusion changes.
- Gateway re-checks auth and re-redacts content before persisting it to
  `cloud_work_text`, **separate from canonical event evidence**. Only
  personal `oauth-sub:` accounts are accepted; enterprise sharing is
  disabled until a separate administrator/legal policy is designed.
- No background requests are made for text while the feature is off.
  When on, the sync worker checks local state about every ten seconds,
  transmits only when the snapshot manifest changes or a reconciliation
  is due, and performs an occasional lightweight reconciliation.
- Server-side approval is independent of local consent: upload requests are
  rejected until the signed-in OAuth account has explicitly approved the
  **specific enrolled device** and the current cloud notice. A device token,
  forged local timestamp or generic account-level approval cannot authorize
  cloud-content uploads. The upload endpoints remain inoperable without the
  explicit operator flag.
- The OAuth-protected public MCP provides `search_work_text(query)`
  and `get_work_text_excerpt(reference)`. Searches filter by authenticated
  actor, do not accept model-supplied account IDs, and apply a second
  redaction/prompt-injection boundary before showing any text.
- A cloud copy lasts **no more than seven days from local observation**.
  Re-uploading does not extend its expiry. Expired/deleted/excluded
  snapshots are hidden immediately by Gateway queries and physically
  removed by the next sync/search or a scheduled cleanup job.
- Unlike the earlier live relay, authorized cloud text can be searched
  while the desktop is shut down, subject to retention.

## Privacy, deletion and failure modes

The user's opt-in is not a substitute for authorization to disclose data
owned by an employer, colleague, patient or customer. GDPR obligations may
arise even for pseudonymized excerpts. Text filters are best-effort, not
a guarantee of anonymization. **Do not enable for regulated/confidential
work** without appropriate authorization, data processing agreements and
security assessment.

- Disable cloud sharing in the local Privacy view: no new uploads. On
  the next successful Gateway connection, the device sends a delete/revoke
  request and all its existing cloud rows/grant are removed.
- Disabling sharing **locally** while the desktop is offline cannot
  immediately revoke earlier uploads: they remain readable until the next
  successful device deletion or their seven-day expiry.
- The hosted backend also offers **immediate OAuth-authenticated personal
  account deletion**, even while every desktop is offline:
  `POST /v1/plugin/work-text-delete` using the signed-in account's OAuth
  bearer. It deletes cloud snapshots and grants across all devices of that
  personal actor. A durable **account lock**, independent of the device clock,
  prevents offline retries from restoring any deleted content.
  Reauthorization requires a new authenticated device-specific OAuth action
  (`POST /v1/plugin/work-text-reauthorize` with the current
  `X-OWG-Consent-Version` notice **and `X-OWG-Device-ID`**) **and a new local
  consent**. These are backend primitives only: a reviewed legal notice and
  authenticated web approval/deletion UI still need to be built.
- Previously delivered AI content cannot be recalled, and deleted SQL records
  may remain in backups according to the provider's retention schedule.
- Deleting a time range locally changes the manifest; existing synced
  cloud rows disappear at the next successful reconciliation.
- Pausing organization sharing, pausing the recorder, or turning off
  master AI access also stops the optional cloud content channel. After a
  successful remote revocation, the desktop cloud toggle is switched OFF;
  resuming ordinary recording does not silently reauthorize old cloud text.
  Both fresh local consent and fresh OAuth approval of that enrolled device
  are needed to start sharing again.
- User-facing deletion does not recall content already supplied to an AI
  conversation or downstream system. Source content is never an instruction
  or execution authorization.

## Independent consent and account-deletion release gates

The desktop UI records versioned affirmative cloud permission. The Gateway
now **also requires a separate OAuth-authenticated approval of the specific
enrolled device** (`POST /v1/plugin/work-text-approve` with both
`X-OWG-Consent-Version: cloud-work-text-v1` and `X-OWG-Device-ID: <device-id>`).
The backend confirms that the device is actively enrolled to that OAuth
subject; device write credentials cannot authorize themselves, and approving
one device does not approve all devices. Account-level deletion invalidates
all device approvals until new signed-in authorization. Device revoke likewise
invalidates its own approval. A fresh local consent epoch is additionally
required after revocation. Concurrent sync/deletion/approval writes are
serialized per actor to prevent stale uploads restoring deleted content.
This is not hardware attestation: a compromised client can still falsify
local timestamps, so users must not approve an unrecognized device.

**Still missing before public enablement:** an intuitive signed-in web
consent screen identifying the enrolled device, showing the published
notice and confirming device-specific approval, plus a signed-in account
Delete Cloud Text control. The API endpoints exist, but
an undocumented HTTP header is not a user-facing consent experience.
A general TOS checkbox alone is not enough.

The account-deletion endpoint is available on the authenticated backend, but
must be exposed in an intuitive signed-in web account screen, with clear
success/failure status. After account deletion, the desktop must clearly
explain that fresh consent is required rather than quietly retrying uploads.
Retest the workflow with a desktop that is offline and later reconnects.

**Merging code behind a disabled operator flag is not the same as activating
content sharing.** The feature must remain disabled until these gates and the
legal/security tests are complete.

## Operator steps before activation

1. Publish reviewed Terms of Service, Privacy Policy and a feature-specific
   disclosure. The proposed language and DPA checklist are in
   `docs/legal/CLOUD_WORK_TEXT_LEGAL_REVIEW_DRAFT.md`.
2. Use a correctly secured, **encrypted-at-rest PostgreSQL deployment**,
   HTTPS, restricted database access, audited access logs, secret management,
   and an appropriate hosting/data-transfer location and vendor agreements.
   OWG's database schema is not application-level end-to-end encrypted.
   SQLite development mode is not an assurance of disk encryption.
3. Explicitly set **`OWG_CLOUD_WORK_TEXT_ENABLED=1`** in both the Gateway
   and hosted plugin environments. No relay key is required. The flag is
   OFF otherwise, and structural evidence continues to function normally.
4. Schedule a daily `python -m gateway.work_text_cloud` cleanup against the
   Gateway's primary database (and reconcile backup retention separately).
5. Test with synthetic browser pages, drafts, excluded hosts, deletion,
   consent/revocation while online/offline, and two separate linked users
   in the *actual* production architecture before rollout.
6. Enforce cloud-only request limits (30 uploads/minute per device, 90 per
   actor), at most 10 approved devices per personal actor, 500 active rows
   and 600,000 characters of page titles/hosts/excerpts across the actor's
   devices. Quota violations roll back their database transaction. Reverse
   proxy abuse controls and service monitoring are still required.
7. Verify the GitHub Actions PostgreSQL concurrency job: simultaneous actor
   uploads, quotas, account deletion and durable tombstones. This does not
   replace production OAuth, browser, backup or retention tests.

## Scope limitations

Search is currently literal token matching, bounded to the latest 1,000
candidate snapshots; it is not semantic search. Each result carries the
capture timestamp, hostname, redacted title, page/draft type and opaque
reference. It cannot reliably know an underlying Gmail message ID or
document identity, nor read native desktop text. More precise resource
linking needs separate, privacy-preserving work.
