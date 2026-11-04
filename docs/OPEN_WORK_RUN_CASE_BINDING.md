# Local run-to-case binding ledger (experimental)

This module adds a durable local operator-confirmed association between an imported automation run and an Open Work case. It is **not** a source-verified join, proof of an outcome, agent execution authority, or customer success. The existing manually selected comparison API is unchanged.

## Integration contract

Call `bind_run(scope, actor, secret, case_ref, run_ref, fingerprint, revision)` only after independently re-admitting the authenticated actor, current case declaration/revision, retained source scope, consent and imported-run snapshot. The module cannot authenticate HTTP requests or infer those grants. Use `get_binding` only after repeating those checks; it refuses expired, mismatched-fingerprint and mismatched-revision associations. Use `revoke_binding` for explicit removal. Return values explicitly mark identity and customer outcomes unverified.

The local SQLite ledger resides in the private authentication directory, with an opaque keyed actor identifier, bounded row count and a maximum seven-day TTL subject to the shorter retention policy. No provider credentials, customer names, raw run steps or case contents are stored. Reads never create the database. A missing retention policy fails closed. Deletion of the local ledger or disconnect/revocation must be coordinated by the integrating authenticated service.

## Outstanding acceptance gates

This first backend slice is **not yet wired to REST/MCP/dashboard** and must not be exposed without authorization, revocation and deletion integration. Independently checked run identity, multi-process grant invalidation, live-provider acceptance, customer-outcome adjudication, and held-out A/B/C commercial trials are separate unfinished milestones. Synthetic unit tests alone do not establish production readiness.

## Authenticated local dashboard REST integration

POST `/v1/open-work/declarations/human-outcomes/imported-run-binding` with an active `owg-session` dashboard session and exact JSON fields: `work_item_ref`, `expected_revision`, `expected_edit_token`, `confirm_historical_review: true`, `confirm_run_comparison: true`, `max_cases` (1–4), `deployment_id`, `run_id`, and `action` (`bind`, `read`, `revoke`). It returns a minimized `binding` (or null for an absent association) and explicit false identity/outcome/authorization flags. The session credential is not persisted; associations are scoped to its opaque digest and become inaccessible from another session.

Every operation re-admits the local source, declaration revision and exact edit token, reads the current imported run and independently retained provider/human histories twice, refuses changed snapshots, and rechecks the source and ledger guard. It never calls a provider, authorizes execution or equates reported success with customer outcome. The route is intentionally dashboard-only, not MCP/agent-exposed. Source disconnect/replacement changes scope and prevents access to old associations; physical cleanup of inaccessible scope rows remains subject to retention and is not proof of immediate erasure. Revocation is explicit. This is a local operator review tool, not independently verified run identity or real-provider acceptance.


## Source-scope revocation

The authenticated Open Work source-change guard now transactionally deletes all run/case bindings for the **old** scope across actors when it detects a disconnect or account replacement during a request. This cleanup does not create an absent ledger and never deletes a different source scope. The old scope is denied even before physical cleanup because current source admission is mandatory. The purge does not call Stripe, import runs or grant agent authority. This is **not** an atomic transaction with provider credential revocation or other local ledgers; if no request executes the guard after disconnect, inaccessible rows may remain until retention expiry. Operator-initiated explicit revocation remains available. Native platform and real-provider acceptance are separate gates.


## Operator bulk case revocation

POST `/v1/open-work/declarations/human-outcomes/revoke-case-run-bindings` using an active local `owg-session` header and exact JSON fields `work_item_ref`, `expected_revision`, `expected_edit_token`, `max_cases` (1–4), `confirm_historical_review: true`, `confirm_case_binding_revocation: true`. Returns `revoked_count` and explicit false identity, customer-outcome and agent-authorization flags. The endpoint re-admits the current declaration, source and human/provider review twice, then deletes only the current dashboard session actor's run associations for that one case in a single SQLite transaction. It requires no deployment/run identifier, so operators can correct mistaken associations after imported runs disappear. It never contacts Stripe, changes other actors/cases, creates a missing ledger or authorizes an agent. A different dashboard session cannot revoke another session's associations. Current source admission and retention policy remain mandatory. This is local review and correction, not independent source-confirmed completion.


## Reviewed case-wide association inventory

POST `/v1/open-work/declarations/human-outcomes/case-run-bindings` from an authenticated local dashboard session with exact JSON `work_item_ref`, `expected_revision`, `expected_edit_token`, `max_cases` (1–4) and `confirm_historical_review: true`. This is a **read-only**, bounded inventory of that session actor's unexpired run associations for one currently admitted case. It does not require remembering run IDs, which allows operators to locate an association before invoking the existing exact-run comparison or individual/bulk revoke endpoints.

The handler repeats the admitted case and retained human/provider-history review, checks the current source and declaration/ledger guard, and returns `Cache-Control: no-store`. Missing ledgers are not created. Results contain opaque run refs and a `current_revision` or `stale_revision` label. **Neither label rechecks imported run fingerprints or establishes identity, source-confirmed completion, a business outcome, or permission to execute.** The separate existing imported-run comparison must be used for that purpose. Source disconnect and case-wide revocation remove access; no cross-session, cross-case or cross-source enumeration is provided. Provider writes and automatic scans are never performed. Real-provider acceptance and exact-head native CI remain separate gates.
