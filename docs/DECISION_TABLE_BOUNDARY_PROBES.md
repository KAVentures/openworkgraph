# R21 — Synthetic numeric boundary probes for decision tables

OWG already evaluates confirmed UNIQUE/FIRST decision tables against
explicitly authorized, caller-supplied case facts. Those cases might never
include the exact value where two numeric rules meet. The new **synthetic
boundary probe** supplements that check: OWG generates a bounded set of
hypothetical numbers and asks the existing exact decision evaluator which
branch, if any, would be chosen.

The results are not business cases or policy-compliance evidence.

## Operator workflow

1. Open a saved, reviewed process in **Process catalog → Check declared
   conditions against case facts**.
2. Select the same explicitly authorized local JSON case file and run the
   existing check. No new source connector, permission or upload is added.
3. Expand **Synthetic numeric boundaries** below the normal supplied-case
   table coverage. The panel reports how many hypothetical numeric points
   produced a UNIQUE gap, an overlap, or FIRST precedence competition.
   It shows up to four hypothetical numeric witnesses for each issue type.
4. Adjust the reviewed source rules if a witness reveals an unintended
   boundary. Any such edit must go through the existing rule confirmation,
   model-revision and human-review requirements.

The same bounded diagnostic is included in the saved-revision case-check
API response as `decision_table_boundary_probes`, alongside the untouched
`decision_table_test_coverage` derived from the user's supplied cases.
Older models without tables keep their exact former report field set.

## Precisely supported scope

A table is sampled only when:

- the table and each referenced rule are already confirmed;
- the caller supplied an explicit effective date within the declared
  [start, exclusive-end) range;
- each row is a **single numeric** `gt`, `gte`, `lt`, or `lte`
  comparison on the **same field**;
- the field can be represented by a simple bounded nested JSON object;
- the table has **no required-decision dependencies**.

The checker evaluates the two bounded-domain ends, the exact declared
numeric thresholds, and a representative number between adjacent thresholds.
There are at most 83 points per table; larger/composite predicates are
not guessed into a simplified model. If a numeric point or interval cannot
be represented reliably by supported JSON numbers, the output records the
number of omitted sample positions rather than silently claiming coverage.

For UNIQUE tables, a generated point producing multiple matches is an
`overlap`; a point producing no matches is a `gap`. For explicitly ordered
FIRST tables, an earlier match intentionally wins. The report separately
counts sample points where more than one row matched, because precedence may
hide a conflicting business-policy interpretation.

Malformed dates, unconfirmed tables, unsupported operators, mixed fact
fields, array indexes and dependent tables result in a **not evaluated**
diagnostic with a fixed reason. No optimistic branch is selected.

## Data separation and limitations

The probe report contains **no case identifiers, case-fact values, field
paths, provider record references, document bodies or credentials**.
Numerical witnesses are generated from already declared numeric rule
thresholds; they are not measurements of actual people or processes.

Every probe reports:

- `source_case_facts_used: false`
- `actual_business_cases_evaluated: false`
- `whole_numeric_domain_coverage_verified: false`
- `business_policy_compliance_verified: false`
- `execution_authorized: false`

A lack of detected gaps or overlaps is **not proof** that a rule set
covers all possible values or that a rule is true, current or lawful.
Fractions, floating-point representability, unmodeled predicates,
cross-table dependencies, null and missing data can change outcomes.
The declared source-document SHA-256 remains human-authored and unverified.
This is not a DMN/FEEL solver, a business-action engine, a deployment
gate or an external provider refresh.

## Acceptance

`tests/test_process_decision_boundary_probes.py` checks exact equality
and both sides of a numeric boundary, overlap, gap, FIRST precedence,
missing/expired dates, unconfirmed and dependent rules, unsupported
predicate shapes, nested numeric fields, report minimization and complete
legacy field compatibility.

`tests/browser/process_decision_tables_local.cjs` verifies that the
real local saved-revision case-check page displays the synthetic review
section, keeps the existing supplied-case report and downloads the
bounded, minimized diagnostics.

The feature must pass complete exact-head CI before merge.
