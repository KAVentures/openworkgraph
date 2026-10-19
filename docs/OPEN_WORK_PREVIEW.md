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
- The ephemeral `owg:w:` preview pointer is **not** a persistent case ID or a
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
