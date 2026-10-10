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
- If the desktop cannot reach the Gateway when cloud sharing is disabled,
  remote deletion cannot happen instantly. Previously synchronized rows
  can remain readable to the signed-in cloud user until a successful
  revocation or their seven-day expiry. This limitation must be fixed or
  clearly accepted before general availability. Deleted records may remain
  in managed-database backups according to the provider's retention terms.
- Deleting a time range locally changes the manifest; existing synced
  cloud rows disappear at the next successful reconciliation.
- Pausing organization sharing also suspends/revokes the optional cloud
  content channel when connectivity permits.
- User-facing deletion does not recall content already supplied to an AI
  conversation or downstream system. Source content is never an instruction
  or execution authorization.

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

## Scope limitations

Search is currently literal token matching, bounded to the latest 1,000
candidate snapshots; it is not semantic search. Each result carries the
capture timestamp, hostname, redacted title, page/draft type and opaque
reference. It cannot reliably know an underlying Gmail message ID or
document identity, nor read native desktop text. More precise resource
linking needs separate, privacy-preserving work.
