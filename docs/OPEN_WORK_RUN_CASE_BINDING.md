# Local run-to-case binding ledger (experimental)

This module adds a durable local operator-confirmed association between an imported automation run and an Open Work case. It is **not** a source-verified join, proof of an outcome, agent execution authority, or customer success. The existing manually selected comparison API is unchanged.

## Integration contract

Call `bind_run(scope, actor, secret, case_ref, run_ref, fingerprint, revision)` only after independently re-admitting the authenticated actor, current case declaration/revision, retained source scope, consent and imported-run snapshot. The module cannot authenticate HTTP requests or infer those grants. Use `get_binding` only after repeating those checks; it refuses expired, mismatched-fingerprint and mismatched-revision associations. Use `revoke_binding` for explicit removal. Return values explicitly mark identity and customer outcomes unverified.

The local SQLite ledger resides in the private authentication directory, with an opaque keyed actor identifier, bounded row count and a maximum seven-day TTL subject to the shorter retention policy. No provider credentials, customer names, raw run steps or case contents are stored. Reads never create the database. A missing retention policy fails closed. Deletion of the local ledger or disconnect/revocation must be coordinated by the integrating authenticated service.

## Outstanding acceptance gates

This first backend slice is **not yet wired to REST/MCP/dashboard** and must not be exposed without authorization, revocation and deletion integration. Independently checked run identity, multi-process grant invalidation, live-provider acceptance, customer-outcome adjudication, and held-out A/B/C commercial trials are separate unfinished milestones. Synthetic unit tests alone do not establish production readiness.
