# Reviewed decision tables

A process designer can attach a bounded **UNIQUE** decision table to an existing
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
   **Synchronize rows** explicitly rebuilds those references.
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
| `overlap` | At least two rows match; row order does not break the tie |
| `unknown` | Missing/incompatible facts leave a competing row undecidable |
| `gap` | Every row is known false; no implicit default branch |
| `unique_match` | Exactly one match and all competing rows are known false |
| `not_on_proposed_route` / `not_evaluated_invalid_route` | Supplied route excludes the decision or does not fit the graph |

Start is inclusive; end is exclusive. The date is caller-declared and not a
verified current business date or timezone conversion. An absent end leaves an
open interval, not a guarantee that the policy remains current. Repeated visits
to the same decision in a proposed looping route are compared against the same
case-fact snapshot; this does not model changing facts between visits.

The report contains row/rule IDs and statuses, plus a target ID only on a unique
match. It never echoes fact values or source-document metadata. A proposed
branch can be compared with the match, but **no route is executed**. Boundary,
overlap and gap tests describe only the supplied finite cases; they do not prove
exhaustive coverage or absence of contradictions across the whole input domain.
Normal rule counts remain rule results; table results are listed separately.
Current-source rule checks do not implement authoritative table/date evaluation.

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

Agent implementation export is explicitly unavailable for models with tables.
The generated runtime also refuses manually injected nonempty table declarations
before planning. Otherwise silently ignoring UNIQUE conflicts or expired policy
could produce a false claim that an approved implementation follows the design.
Existing models without tables, including parallel runtime projects, keep working.
Design readiness lists tables awaiting runtime support and unconfirmed tables.

Future R21 work remains: reusable decision sets/dependency graphs, multi-output
and additional reviewed hit policies, exhaustive domain analysis, source-backed
freshness/effective-date enforcement in generated implementations, and standard
DMN interchange. Full R21 and competitor parity are not complete.

## Research basis and verification

[Camunda's hit-policy guide](https://docs.camunda.io/docs/components/best-practices/modeling/choosing-the-dmn-hit-policy/)
was retrieved successfully on 26 October 2026. It distinguishes UNIQUE (no
overlapping matching rows) from FIRST (row-order precedence), ANY and collective
policies. OWG deliberately supports one explicit policy using its existing typed
predicates, and does not claim compatibility with a complete DMN/FEEL engine.
The broader primary-source competitor comparison remains in issue #370 and
`COMPETITOR_GAP_ASSESSMENT_2026_10.md`.

`tests/test_process_decision_tables.py` covers an independently specified purchase
threshold oracle, exact effective-date boundaries, competing unknowns,
overlap/gap, invalid references/confirmation/bounds, immutable saved history,
minimal audit/auth/tenant checks, unchanged legacy hashes, BPMN remapping and
implementation refusal. Store tests also run against disposable PostgreSQL.
`tests/browser/process_decision_tables_local.cjs` covers the local editor, numeric
boundary/missing checks, downloads and explicit implementation refusal.
`tests/browser/process_decision_tables.cjs` exercises actual editing, confirmation
reset, saving, authorized case checks/downloads, wrong-route/missing/date/expiry
results, held old identity, reader access, WCAG/mobile and removal. Fixtures are
synthetic and certify neither real company policy nor customer integration.
