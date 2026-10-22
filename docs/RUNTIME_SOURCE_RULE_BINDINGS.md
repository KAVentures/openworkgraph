# Reviewed current-source invariants in customer runtimes

Generated SDK projects keep structured rules blocked by default. A customer can
supply a reviewed implementation for rules that must remain true before **every
mapped action** and at route completion. This is a deliberately limited current
state invariant, not a general temporal branch interpreter or policy engine.
The default planner still requests clarification for structured rules.
[Source-aware planning](SOURCE_AWARE_PROCESS_PLANNING.md) optionally uses separately
authorized rule-check results to propose a route; the executor still needs an
explicitly approved exact-artifact intent and fresh source checks.

For example, a customer can require a selected observed pull request to remain
merged before processing downstream tasks. Add the following to the separately
reviewed `bindings.json`; the selected case contains `approved_record`:

```json
{
  "source_rule_checks": [{
    "rule_id": "approved_source",
    "source_type": "github_pull_request",
    "resource_ref_pointer": "/approved_record",
    "mode": "invariant_before_each_action",
    "record_semantics_reviewed": true,
    "non_atomic_reads_reviewed": true
  }]
}
```

The exact saved rule must be confirmed and contain a structured predicate.
Every condition must use `/source/provider`, `/source/record_kind` or
`/source/state`. The customer must establish why the selected observed record
belongs to the authorized business case. Neither an opaque OWG reference nor
an authenticated provider state proves that association. Arbitrary supplied
case facts, dashboard comparison reports and model answers cannot satisfy this
implementation. Unbound used rules retain
`structured_rule_source_fact_implementation_missing`; unsupported paths,
unreviewed bindings and duplicate mappings are rejected before I/O.

Supported source types are `github_pull_request`, `jira_issue`, `stripe_invoice`
and `stripe_refund`, with the existing adapter state vocabularies. Source
adapters remain separately opted in and use their existing local approved
locators and least-privilege credentials. No API enables those grants.

The existing execution intent pins the exact specification, plan, case and
bindings. Separate customer authorization, source-read confirmation, a short
expiry and `OWG_EXECUTION_SOURCE_READ_TOKEN` are required. Existing tool schema,
precondition, provider conditional-write, idempotency, shared independent-review
and replay-journal checks remain in force. The target service must independently
authorize the requested operations.

The executor fetches new receipts before opening the tool transport, immediately
before each mapped action (after any write precondition), and at completion.
It validates the selected reference/type, current-state provenance, known state
and timestamp (at most 120 seconds old, at most five seconds in the future).
False or unknown stops further actions. Missing receipts never satisfy an
absence predicate. Local journals contain only rule IDs and result statuses,
not source values, record references, rule literals or credentials.

These reads are not atomic with remote actions. Source state can change after a
read, and failures cannot undo completed or in-flight effects. Multi-record
rules have separate read times. Use service-side business policies and atomic
conditional operations for requirements that must hold at the write boundary.
A rule whose truth is expected to change during the workflow needs a more
specific implementation. Completion reports keep tool results, current rule
checks and independently reported terminal operations separate from business
outcomes and agent causality.

## Minimized local verifier API

`GET /v1/record-graph/state-receipt?resource_ref=…&source_type=…&confirm_source_read=true`
checks authentication and the existing AI-access/history disclosure lease on
the server, including direct runtime calls. All-saved grants use retained
evidence; selected-range grants constrain lookup to that range; otherwise only
the current run is eligible. An unavailable current boundary is refused. Confirmation
is required, and the normal provider opt-ins and canonical observed-record
identity checks still apply. This API returns the three minimized current
provider fields to an already authorized client; unlike the dashboard rule
comparison report, the receipt includes the actual known state. It returns no
native identifiers, comments, amounts, actor names, timeline or credentials.
Responses use `Cache-Control: no-store` and are not persisted.

Receipts use `openworkgraph.source-state-receipt.v1` and explicitly disclaim
business-case association, historical facts, policy compliance, business
outcomes and execution authority. Unknown identity, source failure, stale state
or an unavailable approved locator returns an unknown receipt. This is an
HTTPS/loopback authenticated response, not a signed policy attestation. Generated
runtimes require a loopback OWG verifier endpoint and perform no automatic retries.

Tests use owned synthetic MCP tools and the production source receipt contract;
no real customer account is contacted or modified. Live customer permissions,
record semantics and workflow acceptance remain required.
