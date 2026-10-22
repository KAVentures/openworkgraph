# Explore a selected invoice's source lifecycle

On an observed **Stripe · invoice** record lane, explicitly select **Read
opted-in invoice lifecycle** and confirm this exact source read. The existing
Stripe restricted read-key/account opt-in is still required. OWG reuses the
existing source adapter: verify the configured account with `GET /v1/account`,
then read the exact observed invoice with `GET /v1/invoices/{id}`. No event-list,
invoice inventory, additional permission, credential input or write endpoint is
introduced. The pinned Stripe read API version remains `2024-10-28.acacia`.
When at least two observed invoice lanes are available, check the invoices to
include and use **Compare selected invoice lifecycles**. Each source record is
independently checked and remains a distinct case; the comparison does not join
invoices into an inferred business case. Changing selection invalidates pending
comparison responses.

Only this separate lifecycle action opts into the new timestamp projection.
Ordinary source-status receipts, record-graph calls, REST/AI context and source
outcome checks keep their previous payloads. The new internal options default
off; existing source opt-in alone does not add history to normal AI context.
Full canonical observed-reference proof and the exact provider/kind/host guard
run before any source call. Unobserved, truncated-proof, wrong-provider, refund
or mismatched-account references cannot trigger an invoice lifecycle read.

Supported invoice fields are:

| Source field | Structural event |
| --- | --- |
| `created` | Invoice created |
| `status_transitions.finalized_at` | Invoice finalized |
| `status_transitions.marked_uncollectible_at` | Marked uncollectible |
| `status_transitions.paid_at` | Invoice paid |
| `status_transitions.voided_at` | Invoice voided |

Only positive integer Unix timestamps convertible to UTC are projected. Missing
fields stay absent, rather than borrowing the time OWG checked the invoice.
Booleans, strings, nonpositive/out-of-range values receive diagnostics. Histories
with chronology conflicts are omitted, including transitions preceding creation,
terminal timestamps preceding finalization, paid/voided timestamps preceding a
recorded uncollectible transition, or histories containing both paid and voided timestamps.
The existing current-state receipt remains separate: omitted lifecycle fields
do not silently change the adapter's already defined operational-state checks.

Lifecycle refs are ephemeral process-secret HMAC tokens scoped to the existing
opaque resource reference, field and timestamp; they contain no native invoice
ID and are omitted from the process report. Invoice/customer IDs, emails, names,
amounts, credentials and raw source payloads are never returned by this projection
or persisted. The source adapter still reports that full audit history is
unavailable; these are selected invoice snapshot fields, not a Stripe Events API
audit or a complete business workflow.

The shared source HTTP logging guard suppresses transport URL/header traces only
within approved source-read contexts. This keeps native invoice URLs out of
debug logs while preserving unrelated/concurrent request logging, including when
a source read fails. It is shared with the selected Zendesk connector.

**Explore Stripe invoice source process** opens the existing process explorer
without another provider read. Maps, variants, frequency/gap overlays and examples
describe the supplied lifecycle fields. Equal-resolution timestamps retain
separate occurrences and explicitly unverified tie ordering. Elapsed gaps are
not employee effort, waiting or measurable savings. A source-reported paid field
does not verify customer bank settlement, business objective or agent causality.
The projection grants no execution authority.

CSV retention controls stay disabled for source previews. A separately reviewed
TO-BE draft can be saved to the process catalog using partial-source provenance,
with no native IDs or new source reads. Ordinary desktop capture, AI-access modes,
canonical evidence, source credentials and existing outcome-verification behavior
remain unchanged.

## API

`POST /v1/process-mining/stripe-selected-invoices` accepts exactly
`{"resource_refs":["owg:r:..."],"confirm_source_reads":true}` under a valid local
dashboard session. Select 1–8 distinct opaque references already supported by
retained canonical observations. JSON bodies are bounded to 2 KiB; duplicate
fields, nonstandard constants, malformed refs, unconfirmed reads and AI bearer
credentials are rejected before source access. Each selected record independently
checks its account, exact object identity and existing source opt-in.

Responses use `openworkgraph.stripe-selected-invoices-process.v1`, no-store
headers, timestamp/conflict diagnostics and the existing minimized mining report,
with explicit false audit-completeness, business-outcome, bank-settlement,
agent-causality, inventory-scan, persistence and execution-permission flags.
At most five lifecycle timestamps per selected invoice can be included.

Validation covers the unchanged default receipt, explicit history option, exact
existing two-read behavior, account/object/provider isolation, no unobserved reads,
missing/invalid/conflicting/equal timestamps, minimized output, strict auth/body
limits and the actual canonical-proof → selected source → explorer → separately
reviewed design/SQLite browser flow. Existing Stripe compatibility and operational
receipt checks still pass. No real Stripe customer account or financial operation
is used by these owned synthetic tests.
