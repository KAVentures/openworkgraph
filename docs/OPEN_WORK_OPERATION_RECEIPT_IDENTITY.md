# Source operation receipt identity consistency

This narrow #247 Milestone 4 / #264 Stage 7 regression guard strengthens the
existing `server/open_work_outcomes.py` operation receipt. It does **not**
introduce a new case graph, provider adapter, permission, or execution path.

A verified operation requires **both** a retained, unambiguous observed
`record_type` for the expected provider/object kind and a fresh, explicitly
source-reported `source_evidence` receipt from that same provider. A missing,
non-string, or different `source_evidence.provider` returns
`availability=unknown`, `reason=provider_receipt_identity_conflict`, and
`operation_verified=false`. If the source receipt supplies an `object_type`,
it must also equal the observed record kind. Older Stripe receipts do not
supply `object_type` and remain supported, because the retained record graph
already establishes their kind; Jira and GitHub supply the field. An absent
source status remains `authoritative_provider_status_unavailable` rather
than being upgraded into an identity success.

Positive retained-record evidence is now admitted only when both
`record_observed` and `record_identity_complete_within_retained_window` are
literal JSON booleans `true`. Values such as `1`, `"true"`, missing/null,
arrays or objects fail closed as `availability=unknown` using the existing
record-not-observed or incomplete-identity reason. The existing conservative
source-event truncation check remains in place. These assertions are about
observed evidence integrity, **not** source/tenant authorization; the trusted
record reader and existing consent boundary remain responsible for that.

The existing exact-record, bounded-history, source-refresh opt-in and
freshness checks remain mandatory. A matching provider and object kind
**alone never proves an operation**: the expected source state and provider
operation detail still have to pass their existing independent checks. Even a
verified provider operation is not evidence that an agent caused it, that a
customer received the intended outcome, or that a business objective was met.

`tests/test_open_work_outcome_receipts.py` contains synthetic conflicting
provider and object-kind fixtures for GitHub PRs, Jira issues, Stripe invoices,
and Stripe refunds, including positive compatibility controls, 128 malformed-positive-flag
provider/field/value combinations, four valid controls, and privacy
assertions. These are deterministic fake-reader tests, **not** authorized
real-provider checks, live tenant isolation proofs, commercial ROI, or
customer-outcome validation. No new network requests or source privileges are
introduced; the endpoint and optional MCP contract remain unchanged.
