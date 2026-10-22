# Current-source rule checks

The local saved-process editor can explicitly refresh one already observed
source record and compare confirmed structured rules with its current status.
This extends [declared rule checks](PROCESS_RULE_CONDITIONS.md) with facts from
OWG's existing authenticated, read-only adapters. It does not reconstruct past
business facts or authorize agent execution.

## Use

Add a confirmed structured predicate to a saved rule, for example
`/source/state eq "paid"`, then save and review the new model revision. In
**Check rules against a current source record**, choose the source type and an
opaque observed record reference. **Load observed source record choices** reads
a bounded local navigation sample only; choosing a row performs no provider
read. An already observed reference may also be entered manually. The picker
is not an exhaustive inventory or source-identity proof.

Explicitly authorize refreshing that selected record, then select **Refresh
selected record and check rules**. Existing provider opt-ins, credentials,
account/site checks and read scopes must already be configured; this action
cannot enable them. A record/type change or Clear resets authorization and
invalidates pending results. Draft edits and model/revision changes also
invalidate results. An already requested read may finish after Clear; its late
result is discarded. There are no scheduled or background source refreshes.

Supported source types and state vocabularies:

| Source type | Current state values |
| --- | --- |
| `stripe_invoice` | `draft`, `open`, `paid`, `uncollectible`, `void` |
| `stripe_refund` | `pending`, `requires_action`, `succeeded`, `failed`, `canceled` |
| `jira_issue` | Jira categories `new`, `indeterminate`, `done` |
| `github_pull_request` | `open`, `closed_unmerged`, `merged` |

Exactly three fact paths are supported: `/source/provider`,
`/source/record_kind`, `/source/state`. Other paths remain unknown, including
absence predicates on unsupported paths. An unavailable or stale source does
not satisfy `not_exists`. Supported clauses retain the same strict comparisons
and three-valued all/any semantics as declared checks. Text-only and unconfirmed
rules remain unevaluated. Manual review conditions remain unevaluated.

## Proof and scope

Each refresh uses the existing record graph's exact retained-record proof.
Conflicting, absent or truncated identity cannot authorize a provider read.
The explicitly selected provider/kind/host is pinned before that read; Jira's
host comes from its approved local locator and must match the canonical
observation. No browser-supplied tenant, URL, credentials or case-fact values
are accepted. The existing adapter rechecks account/site/OAuth permission.
Its normal bounded receipt requests may include optional audit data; this check
uses none of that history, personal information, amounts or native identifiers.

Only known adapter receipt methods and current-state vocabularies are accepted.
The timestamp must have an offset, be no more than 120 seconds old and no more
than five seconds in the future. Otherwise conditions remain unknown.
`facts_source_authoritativeness_verified` refers only to these authenticated
current-record fields. The selected record-to-case association is still a
caller declaration: `case_association_verified` and
`historical_case_facts_verified` remain false. A provider status such as paid,
done or merged does not prove bank settlement, customer success, business-policy
compliance or agent causation.

The result identifies the exact saved process revision and model hash, selected
opaque reference, source type and verification timestamp. It contains condition
statuses/reason codes, not actual source fact values, expected literals, native
IDs, customer identities, comments or credentials. Results and source facts are
not retained in evidence, process knowledge or browser storage. Downloading a
minimized report is a separate action and creates an external copy. No model
provider is contacted and no source-system business write is performed.

The generated agent's source-fact implementation guard is **not** removed by a
successful comparison. Customer runtime bindings, fresh preconditions, case
identity, policy review and execution authorization remain separate. The source
report is not a signed attestation or a transferable execution grant.

## API

`POST /v1/process-catalog/check-current-source-rules?workflow_id=…&revision=…`
requires a live local dashboard session and
`X-OWG-Source-Rule-Read-Authorized: true`. Ordinary AI bearers cannot call it.
The strict JSON object has a 4 KiB limit, rejects duplicate/unknown fields and
accepts:

```json
{"resource_ref":"owg:r:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
 "source_type":"stripe_invoice","case_id":"case_1"}
```

`case_id` is optional and defaults to `case_1`; it must be an opaque label.
An optional `activity_ids` list (1–80 bounded model IDs) limits comparison to a
proposed route. Invalid routes, unconfirmed/text-only rules and selections
without supported confirmed conditions do not request a source refresh. Such
routes remain proposals, not observed-case histories.

The response is `openworkgraph.current-source-rule-check.v1` with `no-store`.
`source_status_refresh_requested` records whether the record graph was asked to
refresh status. `outbound_source_reads_may_have_occurred` is conservative: a
request can fail before any network call or after an authenticated read, so a
precise outbound GET count is not asserted. A newer or deleted process is
reported at check completion without substituting a different model revision.
