# Portfolio dependency schedule review (R15)

The local Automation opportunities panel has **Review dependency schedule risks**, a read-only preview of the dates and dependencies explicitly entered by the reviewer. The same report is available to an authenticated local dashboard session at `GET /v1/automation-pipeline/dependency-schedule`. It does not grant permissions or read live business systems.

## What it examines

For each active idea, the report traverses direct and transitive `depends_on` links. An already implemented prerequisite is not treated as a pending blocker. The review flags:

- A prerequisite whose **target quarter is later** than the dependent idea's declared target quarter.
- A prerequisite with **no target quarter** when the dependent has a target.
- Rejected or on-hold prerequisites.
- Deleted or unknown references in older/malformed saved portfolios.
- Cycles in legacy/corrupt graphs and review depth limits.

Paths use local idea IDs so reviewers can inspect the intervening prerequisites. A quarter mismatch is a planning consistency **candidate**, not a calculated project completion date, assurance of deadline failure, or workforce performance measurement. Even if planned quarters appear consistent, tasks can still be late: OWG has not measured a resource-loaded critical path.

## Boundaries

Normal planning writes already prohibit new dependency cycles. This additional read-only check does not edit plans, reset approval, set outcomes, authorize execution or publish any company data. The scan is bounded to the existing maximum of 1,000 local ideas, returns at most 100 warnings and stops following paths after 20 links. Limits and unresolved paths are disclosed explicitly. No user or source-system identity is inferred from the typed owner/team labels.

A healthy result is **not** proof a delivery target is feasible. The report only checks internal consistency of reviewer-entered labels and must be complemented by real, authorized project planning and source-verified completion evidence.


## Exact prerequisite planning waves

The same review includes an independent, complete classification of active
ideas in prerequisite order. Unlike the bounded list of risk paths, this
classification does not stop at the 100-warning or 20-link display limits.
It examines the retained portfolio (at most 1,000 ideas), with one node per
idea and its declared dependencies.

- Wave 1 contains non-held active ideas whose direct prerequisites are all
  declared implemented, or which have no prerequisites.
- Subsequent waves are one plus the maximum wave of the unfinished
  prerequisites. An idea with two prerequisite paths therefore waits for
  the longer chain, without duplicating the idea.
- A held idea, or an idea with a missing, rejected or held prerequisite,
  blocks downstream dependents. Those ideas have no assigned wave.
- Remaining cycles and ideas depending on them are classified together as
  unresolved, never given a speculative wave. Already implemented ideas
  are treated as satisfied declarations, not independently verified outcomes.

Each row includes its saved revision, state, direct unfinished prerequisites,
explicit blockers, and (when every unfinished ancestor and the idea itself
has a target) the maximum declared target quarter across that chain. An
unknown target stays unknown. That maximum is a consistency indicator, not
a computed completion date. Wave numbers are precedence layers, not equal
duration periods, resource allocation, critical-path estimates or authority
to implement. Unrelated approval or process-revision gates remain in force.

The dashboard exposes one wave or the blocked/unresolved set at a time.
A snapshot hash identifies the exact sorted idea IDs, revisions, states,
dependency sets and target quarters used by the calculation. Refreshing
the portfolio clears the displayed review. No schedule or portfolio state
is changed. The full machine-readable classification is an additive
dependency_waves field in the existing authenticated API response.

Regression coverage includes branching and converging graphs, held/rejected
and missing prerequisites, cycle residuals, unknown target dates, an exact
1,000-node chain, bounded risk-list independence, and browser navigation.
This is not a production scheduling optimizer or independently verified
business implementation plan.
