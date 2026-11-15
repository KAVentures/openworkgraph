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
