# Narrow source-verifiable payment goal (Open Work)

This optional, local-only flow advances #247 milestone 4 and #264 Stage 7.
It is **not** proof of customer satisfaction, bank settlement, discharged
business obligations, agent causality, or general financial ROI. Stripe's
authenticated invoice state is narrower than any of those claims.

## Operator workflow

1. Review a source-derived Stripe case through the existing opt-in local
   dashboard review queue. The case must retain a current explicit human
   review and an enabled human-history retention policy.
2. With an authenticated `OWG-Session` local dashboard session, create or
   revise a case declaration via `POST /v1/open-work/declarations` using
   `goal_code: "verify_operation"` and
   `expected_outcome_code: "stripe_invoice_paid_reported"`.
   All existing CAS fields, review gates and source scope still apply.
   Incompatible goal/outcome pairings are rejected by individual and batch
   create/revision APIs; old declaration codes remain unchanged.
3. Explicitly source-scan and link the observed invoice using the existing
   `POST /v1/open-work/declarations/operation-link`. Retain its short-lived
   signed `record_link` locally; a record absent from the current open
   inventory cannot be presumed paid.
4. Explicitly call `POST /v1/open-work/declarations/source-goals/verify-payment`
   with **exactly** the following JSON keys:

   ```json
   {
     "work_item_ref": "owg:w:<32 lowercase hex characters>",
     "expected_revision": 1,
     "expected_edit_token": "<current 32-character edit token>",
     "confirm_source_verification": true,
     "record_link": {"format": "openworkgraph.local-case-record-link.v1", "...": "the complete previously issued sealed link"}
   }
   ```

   The example placeholders are not valid actual case/link credentials. Supply
   the exact previously returned signed link, not a hand-written one. The API
   validates the current declaration, complete approved dependency DAG, source
   account, CAS and signed link **before** the authorized provider read.
   It independently requests a fresh Stripe invoice operation receipt, then
   rechecks scope/review/link before appending the check to the existing private
   bounded HMAC-chained operation evidence ledger.

   The response is `openworkgraph.local-case-declared-source-goal-verification.v1`.
   `provider_declared_goal_met_at_check: true` is possible **only** for a fresh
   strictly verified provider-reported `paid` receipt and the matching
   explicitly declared narrow goal. An outage, malformed receipt, stale receipt,
   missing provider status or contradiction returns `unknown`, never
   `not_met` or a fabricated customer outcome. No provider write occurs.

5. To review retained evidence without a provider request, call
   `POST /v1/open-work/declarations/source-goals/payment-history` with exactly
   `work_item_ref`, `expected_revision`, and `expected_edit_token`.
   It returns `historical_last_provider_state` and counts, but **always**
   `provider_declared_goal_met_now: false` and
   `fresh_source_recheck_required: true`. The existing
   `/operation-ledger/dispute` endpoint can dispute a check without erasing
   earlier entries; a disputed latest paid check becomes historical unknown.
   The existing outcome portfolio triage includes this new declared goal,
   flags missing/unknown provider checks and marks fresh recheck required.

## Privacy, integrity and compatibility

The ledger stores only opaque case references, keyed record binding, provider
state, timestamps and integrity hashes. It does not store invoice IDs, customer
names, amounts, credentials or raw provider receipts. The existing case
declaration, source disconnect, review revocation, retention and HMAC validation
apply to both endpoints. All responses use `Cache-Control: no-store`.

No automatic polling, cloud sync, agent permissions, source writes or new
background process is introduced. Source status does not establish customer
outcome or causality. Pre/post local guards are **not** an atomic cross-process
grant transaction. Production organization RBAC and real restricted Stripe
test-account acceptance remain separate gates.

## Validation

`tests/test_open_work_case_declared_outcome.py` exercises real FastAPI
endpoints and private SQLite with a **synthetic** source adapter: paid then
outage, historical-only reads, dispute, invalid declaration combinations,
forged links, authentication, review revocation during provider read, source
account changes, stale CAS, minimization and batch validation. Run all
repository-required native Ubuntu/macOS/Windows, browser, collaboration,
scale, accessibility, starter and packaging/release checks on the exact
head before merging. Synthetic source tests do not certify real Stripe,
independently adjudicated customer outcomes or commercial uplift.
