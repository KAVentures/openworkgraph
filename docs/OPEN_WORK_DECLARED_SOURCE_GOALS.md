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

### Fresh provider-state transition audit

Every successful explicit `source-goals/verify-payment` now returns an
**atomically fenced, dispute-aware transition** relative to the prior private
HMAC-validated operation evidence ledger. The response includes
`prior_historical_provider_state`, `prior_provider_check_count`,
`prior_last_check_disputed`, `provider_state_transition`,
`observed_provider_state_changed` and the new retained check count.

`provider_state_transition` is one of:

| Transition | Meaning |
| --- | --- |
| `first_check` | No earlier retained provider check |
| `unknown_to_paid` | Earlier evidence unknown (including disputed), new independent provider check reports paid |
| `paid_to_unknown` | Earlier retained check reported paid, latest check is unavailable/unknown |
| `unchanged_paid` | Both retained previous and newly checked provider state report paid |
| `unchanged_unknown` | Both prior and fresh provider state are unknown |

**Unknown is not unpaid.** `paid_to_unknown` is not proof that a payment was
reversed; it may mean source outage or invalid receipt. `unknown_to_paid`
confirms only the fresh Stripe-reported invoice status, not customer success,
bank settlement, obligation discharge, or agent causality.

Before provider access the endpoint reads and integrity-checks the existing
local evidence. Its expected event history is checked again **inside the
SQLite `BEGIN IMMEDIATE` append transaction**, so a competing check or
dispute prevents a stale transition and refuses the new append. Existing
operation-ledger writers without an expected checkpoint keep their original
API contract. Owner review/source admission is rechecked before and after
provider IO, but this does **not** create an atomic cross-process revocation
transaction across all stores.

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
account changes, stale CAS, minimization and batch validation. Additional FastAPI/private-SQLite tests cover all five transition states, disputed paid baselines, atomic concurrent-dispute refusal, and tampered prior evidence rejected before source IO. Run all
repository-required native Ubuntu/macOS/Windows, browser, collaboration,
scale, accessibility, starter and packaging/release checks on the exact
head before merging. Synthetic source tests do not certify real Stripe,
independently adjudicated customer outcomes or commercial uplift.


## Reviewed prerequisite payment checks (explicit, atomic local history)

`POST /v1/open-work/declarations/source-goals/verify-dependency-payments`
accepts an authenticated **local dashboard session** and a JSON object with
exactly `work_item_ref`, `expected_revision`, `expected_edit_token`,
`confirm_source_verification: true`, and `record_links`. The latter must
contain **one previously issued signed source-joined invoice link per case**
for the root and **all** transitive prerequisites, with a maximum of four
reviewed cases. Every case must already declare
`goal_code=verify_operation` and
`expected_outcome_code=stripe_invoice_paid_reported`.
This is an explicit on-demand action; no automatic scan or polling is enabled.

The operator first reviews each case and issues its individual
`/operation-link` using the existing separately authorized Stripe open-invoice
source scan and exact retained record join. For a root A depending on B, issue
B's link against B's own reviewed revision and A's link against A's reviewed
revision. Send both links (order does not grant authority) in one request
with A's current revision and incarnation edit token. The API processes B
before A and returns a minimized `cases` array of fresh `met`/`unknown`
provider-goal states and transitions, together with counts.

**Before the first Stripe request**, the API verifies all current reviewed
declarations, transitive dependencies, link seals, account scope, distinct
source records and existing private HMAC evidence histories. After **each**
read it re-admits the entire owner DAG and source scope. Unknown/outage is
retained as unknown; it never establishes nonpayment or a reversal. Once all
reads finish, the checked states are appended in **one SQLite transaction**.
Every prior history is compared again under the write lock; a concurrent
check/dispute, capacity limit or write error rolls back **all** new batch
events. Source reads themselves are sequential, **not** an atomic Stripe
snapshot, and a source/review revocation after a local commit remains subject
to the documented best-effort cross-store fencing limitations.

The response explicitly separates `all_declared_provider_goals_met_at_individual_checks`
from true business success. Even when every case was freshly reported paid,
this does **not** prove bank settlement, customer satisfaction, obligation
discharge, causality from an agent run, or authorization to execute anything.
No native invoice IDs, customer names, amounts, credentials or raw provider
receipts are persisted. Local source disconnect/review revocation and retention
continue to govern the underlying operation evidence ledger.

Synthetic FastAPI/private-SQLite coverage lives in
`tests/test_open_work_case_declared_dependency_payment.py` and exercises
mixed paid/unknown results, all-paid transitions, invalid/duplicate/missing
links, incompatible goals, mid-read revocation, and an intervening dispute
that forces atomic batch refusal. Run the repository's full exact-head
cross-platform, browser, collaboration, scale, accessibility, starter and
packaging matrix before merge. Real restricted Stripe test-account and
independent customer-outcome acceptance remain outstanding.
