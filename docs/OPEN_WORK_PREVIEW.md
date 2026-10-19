# Open Work: source-backed, read-only Stripe preview

This is the first **additive** Open Work vertical slice, separate from OWG's
existing Activity, Workflows, repeated workflows, Discovery Mode, procedural
memory, exports and AI-context tools. It is not a replacement for workflow
mapping or an autonomous agent.

## What it does

A **local dashboard session**, after connecting one authorized Stripe account
through Privacy > Optional source connections, can explicitly request a
read-only inventory of source-reported `open` invoices. The inventory includes
records OWG has **never observed on screen**. No general provider scans run on
startup or in the background.

For the pilot API:

```http
GET /v1/open-work/stripe-preview?confirm_scan=true&max_pages=1
Authorization: OWG-Session <authenticated-local-dashboard-session>
```

The active dashboard injects the session header automatically when using its
same-origin API helper; direct developers must use a valid dashboard session.
A normal AI read bearer, device upload token or unauthenticated caller
**cannot** perform the inventory scan. This version does not register a cloud
MCP tool or a general dashboard navigation tab. Exposing these candidates to
a remote agent requires a separate consent/privacy review.

The request must explicitly set `confirm_scan=true` **each time**, even if
Stripe single-record status checks were previously enabled. No new
configuration flag automatically grants broad inventory reads. It uses the
already configured, account-verified restricted read-only Stripe key from the
existing OS credential vault; if that key cannot read invoice lists, the
response is unavailable. No key is accepted from the API request.

## Returned information

The response contains bounded **candidates**, each with an ephemeral,
keyed opaque `owg:w:` reference, source-reported status, and whether a valid
Stripe due date is in the past. It withholds Stripe invoice IDs, customer
identities, amounts, full invoice contents, payment details and credential
material.

- `open` means Stripe currently reports the invoice open, **not** that the
  customer has failed to respond or that contacting them is authorized.
- `past_source_due_date` is a candidate for review, not a verified unmet
  contractual obligation or payment instruction.
- Missing or invalid due dates are explicitly unknown; don't infer due dates.
- `source_coverage_complete=false` means pagination was capped, not that no
  other open invoices exist.
- The local-secret-keyed `owg:w:` preview pointer is **not** a persistent case ID or a
  resource reference that an agent can resolve via `get_record_graph`.
  A future independently authorized case graph must provide stable links.
- All data are read in memory for that explicit request, never persisted in
  OWG events, workflow maps, exports, cloud sync or an evaluation dataset.

Provider reads are restricted to `GET /v1/account` for exact-account
verification and `GET /v1/invoices?status=open&limit=100` with validated
`starting_after` pagination. Max 3 pages per request, default 1. HTTPS,
same-origin checks, response-size caps, timeouts and disabled redirects apply.

## How this fits OWG

The existing work recorder and workflow discovery keep explaining
**how** people and agents accomplish work. Optional source-backed Open Work
adds a different question: **which real business records currently need
attention, including records not present in observed work?**

Later, a separately scoped `get_case_context` can *optionally* link one
source-backed candidate to historical human/agent work and declared policy.
Do not join by email subject, company name, UI title or temporal proximity.
Never infer business authorization from what a person previously clicked.

## Not yet implemented

- A normal-user Open Work dashboard and explicit per-provider setup UX.
- Hosted/cloud or organization-level authorization, tenant OAuth and MCP tools.
- Stable cross-provider business case identity and resolved source locators.
- Email, helpdesk, CRM or project-system inventory providers.
- Assignment, human approval, agent execution, scheduling or writes.
- Independent confirmation of contractual obligation or customer outcome.
- Incremental paging controls for inventories larger than the preview cap.

This is an intentionally restricted **testable first source connector**.
It must not be advertised as a finished cross-business Open Work engine.

## Acceptance

Run `python -m pytest -q tests/test_open_work_stripe_preview.py`, then the
existing cross-platform full suite, packaging, MCP and privacy checks.
A real Stripe TEST account with restricted read-only invoice-list scope is
needed to validate provider behavior outside mocked CI. No real customer
records are needed for the synthetic tests.


## Optional case-context drilldown (stacked extension)

The local dashboard's Open Work preview now provides **Inspect case context**
for a candidate:

```http
GET /v1/open-work/stripe-preview/case?confirm_scan=true&work_item_ref=owg:w:<opaque>&max_pages=3
Authorization: OWG-Session <local-dashboard-session>
```

This call re-reads and validates the currently authorized Stripe account and
bounded open-invoice list; a preview reference is not a permanent source ID.
The request returns `unknown` if the item is not currently found, distinguishing
a full inventory miss from a bounded partial scan. It does **not** infer that
the invoice was paid, the customer accepted it or work was completed.

For an exact current source invoice that was also observed in OWG, the
local-only reference dictionary is consulted using the native invoice ID
*inside the server process*. Only previously registered opaque
`owg:r:` references are returned. Every candidate link is then validated
again against retained canonical event evidence and source identity. Deleted,
expired, conflicting or truncated histories are not treated as confirmed.

The returned case context includes current source-reported open status, a
bounded structural timeline of matching observed work, uncertainty,
instruction to use existing workflow family discovery, and explicit
`agent_execution_authorized=false`, `customer_contact_authorized=false`.
No native invoice ID, customer name, amount or provider credential is exported.

Users can inspect the read-only JSON in the local dashboard and manually
copy it to an AI. There is **no automatic cloud upload** or new MCP tool.
Opening an unrelated workflow mapping view remains exactly as before.
A case context is not a validated SOP: it does not make repetition normative,
and it does not establish a real contractual obligation.

### Limitations

- The `owg:w:` references derive from the local restricted Stripe key and
  may change on key rotation. They do not constitute durable graph IDs.
- The integration is presently **local desktop only** and restricted to an
  opted-in Stripe preview. It is not a generalized email/CRM/helpdesk solution.
- Source invoice-list pages are re-queried to establish each case; inventories
  beyond the bounded cap remain incomplete. API costs/rate limits matter.
- Historical workflows are linked **only by exact provider identity** and
  not by similarity, co-occurrence, titles or unverified time-based joining.
- Consent, access control, retention and source credential revocation must
  continue to be enforced by the existing local layers.


## Versioned Stripe source contract and reference lifetime

All three read-only Stripe calls (account verification, exact record receipt,
open-invoice inventory) send `Stripe-Version: 2024-10-28.acacia`. This schema
documents the `invoice.paid` boolean alongside `status`, allowing the
preview's consistency check to fail closed for malformed provider data
without relying on a merchant account's changing default API version.
Tested with synthetic local HTTP streams; **a real restricted TEST key
against Stripe is still needed before production acceptance**. Changing
the pinned API version requires a deliberate upgrade and new acceptance test.

Candidate `owg:w:` references are now derived with a separate, high-entropy
local OWG HMAC secret stored with restricted local-auth permissions. Rotating
the Stripe restricted key therefore **does not** change a candidate reference
for the same Stripe account and invoice. A different OWG installation gets
different references. Clearing the local OWG secret intentionally invalidates
the mapping; historical ephemeral references issued by earlier versions
were key-derived and cannot always be recovered after an old Stripe key
was discarded. These references are still opaque hints to a current
authorized read, not permanent permissions or persistent case records.
