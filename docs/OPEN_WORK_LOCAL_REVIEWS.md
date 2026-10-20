# Local Open Work review intentions (experimental)

This is a **human-triggered local review queue**, not an organization-wide
case manager, provider connector or agent execution tool. It is an additive
extension to OWG's workflow mapping/discovery, which remains unchanged.

A dashboard user may open **Advanced → Privacy → Open Work**, explicitly scan
their opt-in read-only Stripe source and choose **Save for human review** for
one currently source-reported open invoice candidate. OWG re-reads the
authorized, account-pinned bounded invoice inventory immediately before
saving. If the candidate cannot be independently re-verified within the first
three pages, no review is saved and the UI reports uncertainty.

The only stored fields are an opaque `owg:w:` reference, one of three
**human-selected** review intents, creation/expiration timestamps and
fixed safety declarations. There are no native Stripe IDs, amounts, names,
raw emails, customer data, credentials, model prompts, policy text or source
snapshots. The local file is stored with restrictive OS permissions in OWG's
auth directory. The list never polls Stripe: even immediately after a
successful save, the **current source state is unknown until explicitly
rechecked**.

- Maximum 50 saved review intentions on one desktop.
- Expiration: **no more than 7 days** and never longer than the user's
  human-history retention period. Session-only retention disables durable
  review saving and an old queue is scrubbed when the subsystem is accessed.
  The queue never silently overrides the existing privacy choice.
- A user can forget an individual review from the Advanced dashboard.
- Connecting or disconnecting the Stripe source clears all locally saved
  review intentions, preventing an old account's intentions from being
  associated with a new source.
- Open Work review intentions are not part of the canonical event store,
  run memory, workflow clusters, exported traces, synced Gateway data or MCP.
- An outstanding invoice is **not** proof that follow-up is required or that
  payment is owed. The review intent is solely the owner's request to assess
  whether any action makes sense; it grants no agent/customer-contact rights.

All routes require the existing privileged **local dashboard session**,
never an ordinary AI access bearer:

- `GET /v1/open-work/reviews` — list local review intents, no provider read
- `POST /v1/open-work/reviews` — explicit `confirm_scan=true` and
  `confirm_save=true`, plus a current provider status re-read, before saving
- `DELETE /v1/open-work/reviews/{owg:w:opaque}` — forget without provider read

This is a limited *single-installation* review prototype. It does not yet
satisfy issue #247's durable, tenant/actor-scoped, cross-provider case graph,
declarative SOPs, verified customer outcomes, or agent A/B/C uplift study.
That broader work requires a different explicit organizational scope and
deletion/retention acceptance review before being called production-ready.
