# Open Work business-case graph (provider-neutral projection)

This is an **additive case-context extension** to existing OpenWorkGraph.
It never replaces the recorder, workflow discovery/mapping, procedural
memory, workflow exports, Discovery Mode, or MCP continuity.

`server/open_work_model.py` provides `compose_case_graph` for source-backed
case candidates from **any** provider. The caller supplies:

1. A previously created opaque `owg:w:` work-item reference.
2. An independently authenticated, timestamped, source-reported status.
3. Optional *exactly matched* and *retained* OWG observed activity timelines.
4. Optional separately owner-approved, structurally identified policy.

The graph separates a `business_case` node, a `source_status_receipt`
node and zero or more `observed_activity` nodes. A source-reported state can
be verified as a source observation without proving contractual obligations,
customer acceptance, agent success or human handoff. Historical work does
not create an executable instruction or policy. No execution permissions or
source write capability are exposed.

The projection copies a strict whitelist of structural fields. It omits
untrusted titles, raw source identifiers, customer names, payment amounts,
policy text, provider credentials and tool output. Its bounded timelines can
be presented to a human or optionally exported through already authorized
interfaces after an explicit user action.

Stripe is the first real inventory adapter; a synthetic helpdesk `pending`
source demonstrates provider neutrality in tests. **There is not yet a
production helpdesk/email/CRM connector**. New sources must independently
implement secure OAuth/provider-scoped grants, robust pagination, retention
and provenance without assuming absence of observed work proves an obligation.

The current graph is an **on-demand read-only projection**, not a persistent
organization-wide process graph, cross-device identity system or agent
scheduler. An organization-level durable graph, OAuth connection management,
outcome receipts and matched real-agent evaluations remain separately gated
roadmap items in issue #247.

Acceptance: tests must preserve previous workflow mapping, privacy controls
and negative evidence semantics, and should not claim real customer outcomes
from provider status or model telemetry.
