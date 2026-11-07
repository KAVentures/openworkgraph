# Supplied-case decision-table test coverage (R21)

OpenWorkGraph can already evaluate **reviewed structured decision tables**
against a batch of user-provided case facts without retaining those facts,
calling external services, or authorizing execution. This feature makes
that existing workflow useful for *testing* a process: an analyst can now
see which decision branches were actually selected by their test cases,
which remain untested, and which inputs produced gaps, ambiguous matches,
unknown results or blocked dependencies.

## Workflow

1. Open a saved, reviewed process revision in **Process catalog**.
2. Under **Check declared conditions against case facts**, select an
   explicitly authorized JSON file containing 1–50 opaque `case_id` values
   with `facts`, optional `activity_ids` and a table `as_of_date`.
3. Confirm authorization for local analysis and click **Check facts against
   saved rules**. This uses the existing protected
   `POST /v1/process-catalog/check-rules` endpoint.
4. Before the individual case details, OWG now shows a collapsible
   **decision-table test coverage** summary for each declared table. Expand
   it to inspect selected and matched counts per declared row, never-selected
   rows, status frequencies, and whether the table was actually evaluated
   for each supplied case.
5. Download the existing minimized report if needed. It contains only
   model identifiers, structural statuses, matched/selected row counts
   and digest provenance—not fact values or external sources.

## Semantics

The API adds `decision_table_test_coverage` only when the reviewed model
has decision tables:

- `cases_checked`: total supplied cases examined for that decision.
- `evaluated_case_count`: cases where explicit row statuses are available;
  invalid proposed routes, missing dates, expired/unconfirmed tables,
  unresolved prerequisites and conflicting dependency facts can prevent
  evaluation. `not_evaluated_case_count` is the complement.
- `status_counts`: exact counts for UNIQUE/FIRST decisions including
  `unique_match`, `first_match`, `gap`, `overlap`, `unknown`,
  `dependency_unresolved`, `dependency_fact_conflict`,
  `unconfirmed`, `expired`, and route refusal statuses.
- `selected_case_count`: cases where a **single outgoing branch was
  selected** under that decision's hit policy, not just a matching rule.
- `selected_row_count`, `declared_row_count` and
  `never_selected_row_ids`: selected/available distinct declared branches
  in this finite test batch. A row that was never selected is *untested
  for selection*, **not proven unreachable or incorrect**.
- `rows[].selected_cases`: number of supplied cases that selected that
  exact row.
- `rows[].matched_cases_even_if_ambiguous`: number of cases where the
  declared predicate individually matched, including UNIQUE overlap or
  a lower-precedence match in a FIRST table. This can be **larger than**
  `selected_cases` and does not by itself authorize a branch.
- `coverage_basis="caller_supplied_case_facts_only"`,
  `exhaustive_input_domain_coverage_verified=false`,
  `source_facts_authoritativeness_verified=false`, and
  `execution_authorized=false`.

A dependent table with a blocked prerequisite is counted as unresolved
and unevaluated; OWG never invents the prerequisite's result. The
pre-existing `decision_table_as_of_date`, process revision, content SHA,
rule case-level report, and no-retention flags remain unchanged. Models
with no decision tables retain their previous JSON response structure.

## Trust boundary and limitations

User-provided facts are **not independently sourced or verified**. The
existing file-input authorization checkbox, local dashboard-session
permission, body limits, privacy treatment, non-retention and revision
isolation are unchanged. The UI builds text nodes rather than injecting
user-supplied row IDs into HTML. No new capture, sharing, storage,
external AI call or provider write is enabled.

A finite hand-authored test matrix cannot prove full input-domain
coverage, unbiased decisions, policy compliance, real-world process
conformance, financial outcomes or execution readiness. In particular,
100% of a table's rows being selected in one batch does **not** imply
that every possible input is uniquely handled. Independent review and
real-provider validations remain separate requirements.

## Acceptance

`tests/test_process_decision_coverage.py` tests UNIQUE and ordered
FIRST hit policies, individually matched but unselected rows, unknowns,
gaps and overlaps, expired/unconfirmed decisions, invalid proposed
routes, dependency ordering, unchanged no-table outputs and safe
user-facing rendering. Native Python/JS, browser, accessibility,
packaging and scale checks must pass for the exact PR head before
merge. This is a functional testing and analysis capability, not a
standalone decision engine or full DMN/Signavio parity certification.
