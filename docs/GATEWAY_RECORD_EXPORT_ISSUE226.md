# Issue #226: organization-scoped record export (experimental)

Endpoint: `GET /v1/record-graph/export?resource_ref=owg:r:...`

This extends the existing **self-hosted Gateway**. It retrieves an exact, opaque
record reference from already-synced, retention-permitted evidence and returns a
content-minimal JSON timeline with structural actions and observational counts.
It is deliberately **not** an external source-of-truth integration.

## Access boundary

- Authenticated, explicitly issued Gateway *integration* token with
  `evidence:read` is required. A device upload token cannot read it.
- The organization is inferred from the authenticated principal, never from a
  path or query parameter. An actor-restricted integration is limited to that
  actor. An unrestricted organization-level integration retains its existing
  `evidence:read` capacity; this endpoint cannot give it new access.
- All pages go through existing Gateway query retention filtering, including
  changes during cursor pagination. Revoked tokens immediately stop working.
- Results omit native case identifiers, user names, page text, screenshots,
  clipboard values, customer fields and source credentials.
- No provider credentials or local locator dictionary is fetched. A
  missing/stale/source-unavailable result is `unknown` rather than guessed.
- The endpoint does not correlate two different OWG device-specific opaque
  IDs that refer to the same business record. That requires a separately
  authorized, provider-issued identity or administrator-approved mapping.
- Every read writes an existing standard Gateway **access audit** event, but
  no joined record-receipt database, workflow memory or new background sync.

## Why this is not closing issue #226

Source-of-truth billing/helpdesk outcome connectors with organizational OAuth,
per-provider revocation, independent audit-source receipts, consented
cross-provider mappings and genuinely verified human handoff acceptance remain
unfinished. Evaluation must compare A/B/C agents using the same business
facts, log actual OWG retrieval, run source-log ablations, and test held-out
private scenarios. Implement those in separate PRs without silently promoting
a structural work trace into business authority.
