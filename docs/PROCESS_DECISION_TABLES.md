# Reviewed decision tables

A process designer can attach a bounded **UNIQUE** (default) or explicitly ordered **FIRST** decision table to an existing
exclusive decision activity. Each row references the process's existing structured
business rule and its outgoing target. This connects declared business choices to
the graph without copying predicates or inventing conditions from observed paths.
It does not authorize automation or establish company policy.

## Use the shared process portal

1. Open an editable process in `/processes`, then **Edit selected design**.
2. Give every outgoing decision branch a distinct structured rule. Use the
   existing condition editor and confirm each rule after reviewing it.
3. Choose the decision under **Decision tables**, then **Add decision table**.
   Rows are taken from its exact outgoing branches. After changing branches,
   **Synchronize rows** explicitly rebuilds those references. For FIRST, surviving
   row IDs and their declared order remain stable; newly added branches append last.
   Select FIRST explicitly and use **Move up / Move down** to review precedence.
   Policy changes and every move clear table confirmation and outer design review.
4. Enter an effective start and optional exclusive end as `YYYY-MM-DD` calendar
   dates, plus a short source-document label and the reference document's SHA256.
   The reference document is not uploaded, fetched, retained or verified by OWG.
   A human-provided digest identifies the declared source basis; it cannot prove
   the document's contents, current authority or your permission to apply it.
5. Confirm the table, preview the design, and explicitly review organization
   sharing before saving. Semantic edits clear table confirmation and the design
   and sharing checkboxes. A new saved revision requires its own independent
   design review. That review is separate from business-policy approval.

The local desktop process catalog uses the same table editor and minimized
case-check renderer. Its saves remain local unless explicitly exported/shared.

Read-only users can inspect the declarations. Existing tenant-wide process-read,
editor/reviewer roles, revision CAS, audit, archive and deletion rules still apply.
There is no additional table database, implicit evidence synchronization or grant.

## Check supplied cases

Use the existing authorized case-facts file flow. The following example checks
both sides of a numeric boundary, a missing value and a proposed wrong branch:

```json
{
  "as_of_date": "2026-10-26",
  "cases": [
    {"case_id": "case_1", "facts": {"amount": 99}},
    {"case_id": "case_2", "facts": {"amount": 100}},
    {"case_id": "case_3", "facts": {}},
    {"case_id": "case_4", "facts": {"amount": 100},
     "activity_ids": ["choose", "accept"]}
  ]
}
```

These labels, amounts, date and proposed route are supplied by the caller. They
are not associated with observed executions or system-of-record cases by OWG.
For a table with `amount < 100 → accept` and `amount >= 100 → escalate`, the
first two cases produce unique matches, the third is unknown, and the last
reports a disagreement with the supplied branch. String `"100"`, null and
boolean `true` are not coerced into numbers.

The check is available through the existing local
`POST /v1/process-catalog/check-rules?workflow_id=…&revision=…` and shared
`POST /v1/human/processes/{id}/check-rules?revision=…` routes. Existing dashboard
or delegated process authentication and the explicit
`X-OWG-Case-Facts-Authorized: true` header remain required. Shared checks verify
current tenant/read permission before reading the body and again in the store.
The file limit remains 128 KiB on the Gateway, with at most 50 cases, bounded JSON
facts and opaque `case_1` style labels. Facts and reports are not retained. A shared
audit contains operation/revision/counts, never facts, source labels or decisions.
The downloadable report includes exact process revision/content and model hashes.

Each table returns one of:

| Result | Meaning |
| --- | --- |
| `unconfirmed` | Table or referenced rules are not all confirmed; no rows evaluated |
| `date_required` | No explicit case-check date; no wall-clock default |
| `not_yet_effective` / `expired` | Outside the declared interval; no rows evaluated |
| `overlap` | UNIQUE: at least two rows match; row order does not break the tie |
| `unknown` | UNIQUE has an undecidable competitor, or FIRST has an unresolved earlier row |
| `gap` | Every row is known false; no implicit default branch |
| `unique_match` | UNIQUE: exactly one match and all competing rows are known false |
| `first_match` | FIRST: selected row matches and every earlier row is known false |
| `not_on_proposed_route` / `not_evaluated_invalid_route` | Supplied route excludes the decision or does not fit the graph |

Start is inclusive; end is exclusive. The date is caller-declared and not a
verified current business date or timezone conversion. An absent end leaves an
open interval, not a guarantee that the policy remains current. Repeated visits
to the same decision in a proposed looping route are compared against the same
case-fact snapshot; this does not model changing facts between visits.

The report contains row/rule IDs and statuses, plus a target ID on a unique
match or established first match. It never echoes fact values or source-document metadata. A proposed
branch can be compared with the match, but **no route is executed**. Boundary,
overlap and gap tests describe only the supplied finite cases; they do not prove
exhaustive coverage or absence of contradictions across the whole input domain.
Normal rule counts remain rule results; table results are listed separately.
Supplied-case checks use the explicitly supplied date. They do not establish current source state; generated projects use the separate binding and UTC-clock contract below.

Disconnect clears page memory immediately even while a request is pending. A
response from the previous identity cannot repopulate the page or clear the next
identity's state. This does not undo an already accepted server-side write.

## Portable declarations and limits

Tables are an optional `decision_tables` field of the existing process model.
Omitting tables (or supplying an empty list) preserves the normalized payload,
model/specification hashes and behavior of pre-feature designs. When present,
tables and their basis, dates and confirmation are part of that model's digest.
Limits: 12 tables per model, one per decision, 2–40 rows per table, existing
40-rule/40-activity/100-transition graph limits, eight bounded conditions per rule.
Row IDs, rule IDs and targets must each be unique within a table and cover all
outgoing branches exactly once. Confirmed tables require confirmed predicates.

JSON and the plain-text process document retain full declarations. BPMN exports
include non-executable OWG documentation at the exclusive gateway, with explicit
flow references. OWG reimport remaps node/flow/rule IDs, retains predicates,
source basis and dates, and **clears all rule/table confirmation**. Duplicate,
malformed or unresolved recognized declarations are refused. Reimport also
preserves a preexisting manual documentation-review guard without duplicating it.
This documentation is **not DMN XML, FEEL, or an executable BPMN expression**.
Other tools may not preserve OWG documentation; review their output before use.

## Generated implementation and current-source planning

Both the local catalog's **Download OpenAI Agents project** and the shared
portal's **Download SDK implementation project** include table support. Shared
export still requires an active, independently reviewed exact revision. The spec
adds `decision_table_contract: openworkgraph.current-source-unique-decision.v1`
only when tables exist; table-free normalized model and spec hashes remain
unchanged. Injecting tables without the contract is refused. The portable project
includes the same process/table validators and predicate evaluator used by OWG.

Generation does not make a table executable by itself. Bindings remain disabled;
each table template has no selected source type/reference and all review flags
false. A customer must separately review a binding such as:

```json
{
  "decision_table_checks": [{
    "table_id": "current_record_decision",
    "source_type": "github_pull_request",
    "resource_ref_pointer": "/selected_record_ref",
    "mode": "unique_route_before_each_action",
    "record_semantics_reviewed": true,
    "non_atomic_reads_reviewed": true,
    "utc_effective_dates_reviewed": true
  }]
}
```

The case pointer selects an already observed `owg:r:` record, not its alleged
state. Every competing row must be confirmed and use only `/source/provider`,
`/source/record_kind` or `/source/state`. Supported receipt types are GitHub pull
requests, Jira issues, Stripe invoices and Stripe refunds, through OWG's existing
separately opted-in authenticated local source-read endpoint. Amounts, credit
scores and other arbitrary case facts remain unsupported and block implementation
unless a customer develops a separate workflow-specific implementation. Binding
one matching row alone cannot bypass an unsupported competing row.

All rows of one table use **one receipt** for the customer's selected record.
The consumer rechecks its exact identity, provider/kind, receipt scope and false
authority flags, refresh request and freshness (at most120 seconds old, at most5
seconds ahead). It evaluates all rows with the declared UNIQUE or FIRST semantics.
Missing or stale receipts, UNIQUE unknowns or overlaps, unresolved FIRST prefixes,
and uncovered states cannot support a route. Even
negated-existence predicates cannot turn an unavailable receipt into a match.
Dates are checked against the consumer's current **UTC calendar date**, after
the read returns, with inclusive start and exclusive end. No case-supplied date
or planner timestamp can override that clock. This clock must be trustworthy;
a policy digest or clock date does not prove the document's current authority.

The binding declares the selected branch as a **current-state route invariant**:
it must stay uniquely selected before transport begins, immediately before any
write precondition, after that precondition before its action, before each other
action, and at route completion. Each check reads afresh. Every visit to a table
in a looping route must select the same target; changing branch state over time
needs a workflow-specific temporal implementation. Decisions outside a supported
parallel block use actual token-replay transitions rather than adjacent firing
positions. Existing graph validation still refuses decision forks inside parallel
branches. The runtime schedules one business activity at a time.

Use the existing `plan_sources.py` consent flags to read source state and send
minimized table ID/status/selected-target results to the chosen model. Receipts,
provider field values and credentials are not added to model context. The process
and chosen case input still go to that model under the command's existing explicit
consent; a case can itself contain sensitive references. The model has no business
tools, and independent checks refuse a proposed wrong branch, expired table,
unbound table or receipt that aged out during planning/catalog revalidation.
The ordinary `agent.py` path requests clarification for visited tables. It never
substitutes arbitrary caller facts for a source context.

`evaluate_sources.py` supports up to eight reviewed cases with these bindings.
All cases, expected paths and required bindings are preflighted before any source
or model calls. Comparisons independently recheck table contexts, so an old valid
proposal cannot pass after receipt expiry or source/case/model mutation. Customer
expectations remain declarations, not independently established business truth.

Execution still needs the existing separately reviewed exact spec/plan/case/
bindings intent, expiring authorization, source-read confirmation, current shared
catalog review, tool mappings, provider permissions, conditional-write contracts,
idempotency and independent terminal-operation receipts. Table approval grants
none of these. Changed or unavailable decisions stop subsequent work; completed
or in-flight effects cannot be undone. Source reads and target writes are **not
atomic** and do not prove that records belong to the same business case. Service
policy and concurrency controls remain necessary.

Journals retain bounded IDs/statuses/date and route agreement, not raw receipts,
record refs, policy text or values. The existing monitor understands table-check
records and treats a failed check as needing review, including completion drift.
Imported journals remain customer reports, with outcome verification unknown;
operational receipts do not establish business objectives, agent causality or
realized value. Design findings list tables requiring reviewed source bindings,
not a readiness approval.

Future R21 work remains: reusable decision sets/dependency graphs, multi-output
and other reviewed hit policies, arbitrary source fact domains and temporal branch
semantics, exhaustive domain analysis and standard DMN/FEEL interchange. Full R21,
real-provider/customer acceptance and competitor parity remain open.

## Research basis and verification

[Camunda's hit-policy guide](https://docs.camunda.io/docs/components/best-practices/modeling/choosing-the-dmn-hit-policy/)
was retrieved successfully on 26 October and rechecked on 27 October 2026. It distinguishes UNIQUE (no
overlapping matching rows) from FIRST (row-order precedence), ANY and collective
policies. OWG supports bounded UNIQUE and FIRST policies using its existing typed
predicates, and does not claim compatibility with a complete DMN/FEEL engine.
The broader primary-source competitor comparison remains in issue #370 and
`COMPETITOR_GAP_ASSESSMENT_2026_10.md`.

`tests/test_process_decision_tables.py` covers an independently specified purchase
threshold oracle, exact effective-date boundaries, competing unknowns,
overlap/gap, invalid references/confirmation/bounds, immutable saved history,
minimal audit/auth/tenant checks, unchanged legacy hashes, BPMN remapping and
disabled implementation export. Store tests also run against disposable PostgreSQL.
`tests/browser/process_decision_tables_local.cjs` covers the local editor, numeric
boundary/missing checks, downloads and a disabled implementation-project download.
`tests/browser/process_decision_tables.cjs` exercises actual editing, confirmation
reset, saving, authorized case checks/downloads, wrong-route/missing/date/expiry
results, held old identity, reader access, WCAG/mobile and removal. Fixtures are
synthetic and certify neither real company policy nor customer integration.

`tests/test_process_decision_runtime.py` runs complete generated-project suites
with both linear and parallel designs. The included `test_decisions.py` covers
competing-row/order/unknown/receipt/date boundaries, exact bindings and consent,
state changes after a write precondition, completion drift, minimized journals,
real SDK planning and independent source-backed shadow comparison. The actual
MCP transport acceptance in `test_process_customer_runtime.py` includes reviewed
shared decision tables with linear, parallel and nested parallel schedules.

## Ordered FIRST decisions

FIRST selects the first matching row in the explicitly declared row order. OWG
withholds selection when an earlier row is unknown: that row might still win.
Later unknowns or matches cannot displace an established first hit. For example:

| Ordered row states | FIRST result | UNIQUE result |
| --- | --- | --- |
| match, match | first row | overlap, no selection |
| unknown, match | unknown, no selection | unknown, no selection |
| match, unknown | first row | unknown, no selection |
| false, match | second row | second row |
| false, false | gap, no selection | gap, no selection |

FIRST reports `first_match`, the selected row ID and target, or an unknown
prefix's blocking row ID. Reports retain codes/IDs, never input facts. Dates,
confirmation, exact outgoing-branch coverage and all existing size bounds still
apply. There is no implicit default, truth coercion, or inferred business rule.
Reordering rows or changing policy changes the exact model/revision digest and
requires a new independent shared-design review. Imported BPMN retains the
ordered declaration but clears confirmation; this is an OWG BPMN extension, not
DMN XML interchange or a BPMN execution engine.

Generated projects containing FIRST use the distinct contract
`openworkgraph.current-source-ordered-decision.v1`. All-UNIQUE projects keep the
existing `openworkgraph.current-source-unique-decision.v1` contract and binding
shape. Every FIRST runtime binding explicitly requires
`mode: first_route_before_each_action` and `row_precedence_reviewed: true`, as
well as the existing record-semantics, non-atomic-read and UTC-date reviews.
Example bindings set precedence review false and project execution disabled.
An old UNIQUE binding cannot silently become FIRST. Mixed-policy projects use
the ordered contract with policy-specific bindings for each table.

Runtime checks use one current, customer-selected observed source record for all
rows, with the same restricted source paths, permissions, receipt freshness,
pre-action rechecks and completion-drift checks. Caller-supplied case facts do
not substitute for that source. Current receipts do not independently establish
business-policy authority or association of the selected record with a case.
A first match neither grants a tool nor proves an operation's outcome. Invalid
or empty receipts cannot establish a negated/default match. Journals record
policy and row IDs, without private source values; monitoring rejects
contradictory FIRST row/target/policy metadata.

Public reference: [OMG DMN 1.4](https://www.omg.org/spec/DMN/1.4/PDF), formal/23-03-01,
April 2023, printed pages 68–69: FIRST returns the first matching rule in rule
order. Reviewed 28 October 2026; downloaded PDF SHA256
`59137e89fd81e193b03b016bb53c525ceebfda2d2dbceb2cf15147d3da114e6a`.
OWG's earlier-unknown refusal is an explicit conservative partial-fact behavior.
ANY, PRIORITY, COLLECT, multi-output rules, reusable decision dependencies,
FEEL, complete DMN import/export and exhaustive domain coverage remain outside
this implementation. No proprietary implementation or interface assets are used.

`test_first_decision_policy.py` checks all 6,561 eight-row three-state vectors
against an independent prefix oracle, plus dates, review/CAS/role invalidation,
HTTP consent/isolation, minimized exports and BPMN round trips. Portable-project
tests run FIRST and UNIQUE with actual offline SDK planning, customer-authorized
synthetic execution, receipt/expiry/drift guards and tampered-journal checks.
`first_decision_policy_local.cjs` covers explicit selection, overlap, keyboard
ordering, stable resynchronization, unknown-prefix changes, held reports and
WCAG/mobile. The shared browser runs with `OWG_TEST_FIRST_POLICY=1` to cover the
same shared review, tenant, export and identity boundaries as UNIQUE. These
synthetic results certify no customer's policy or deployment.
