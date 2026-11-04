# Optional durable provider check during bound-run review

The existing local dashboard POST endpoint
`/v1/open-work/declarations/human-outcomes/verify-bound-run-source`
remains **read-only by default**. To retain the source verification in the
private case-level operation ledger, include `"persist_provider_check": true`
in the otherwise unchanged authenticated, explicitly confirmed request.

The operator must still supply a current reviewed case revision/edit token,
signed exact-record link, deployment/run IDs, and all three explicit review
confirmations. This is a **local dashboard-session-only** operation. It never
executes an agent action, changes a Stripe invoice, or syncs data to a cloud.

Before contacting Stripe, the route checks the current actor's durable run
binding and snapshots the existing HMAC-protected case evidence history. After
the provider read, it re-admits source, historical review, signed record link,
run fingerprint, binding and case revision. When persistence was requested,
it checks the binding again and appends the provider state using the ledger's
transactional expected-history compare-and-swap. A concurrent evidence edit
fails rather than silently overwriting or reordering history.

The response adds `provider_check_persisted`,
`case_provider_evidence_history`, and
`provider_evidence_run_identity_independently_verified: false`.
The persisted receipt belongs to the **case** and remains available through
the existing authenticated operation-ledger history API. It does **not**
establish that the imported run caused the provider operation, independently
identify the run, prove a customer outcome, or create execution authority.
Provider failures are recorded as unknown, never as paid.

Source disconnection/replacement, retention expiry, review deletion and
revoked bindings must continue to deny current access. This change does not
claim atomicity across the run-binding and operation-evidence SQLite files;
the final post-write source check is a safety gate for the response, not a
distributed transaction. Independent run-identity receipts and real-provider
acceptance remain future #247 milestone 4 gates.
