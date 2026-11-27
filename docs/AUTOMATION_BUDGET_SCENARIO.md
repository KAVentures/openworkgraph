# Budget-constrained opportunity portfolio planning (R15)

From **Automation pipeline** choose **Plan a budget-constrained portfolio**.
The lab evaluates selected local opportunities using their saved planning
budget, current revision, and declared dependencies. It does not change
ideas, reserve funds, approve budgets, or authorize implementation.

Select one currency and one budget basis (estimate or approved budget).
Enter the maximum spending and number of ideas, select 1–18 candidates,
assign each a reviewer-entered integer priority from 0 to 100, and mark
any required ideas. Include unfinished prerequisites as candidates.
Implemented prerequisites are treated as already satisfied for the
scenario; this is based on the recorded state, not verified completion.

The solver enumerates all subsets (at most 262,144). An eligible subset
must include every required idea and every selected idea's unfinished
prerequisites, satisfy the count limit, and stay within the budget.
Dependencies are closed transitively. Missing budgets, mismatched
currencies/bases, ineligible states, missing prerequisites and cycles
block affected candidates; nothing is silently assigned a zero cost.

The objective is explicit: maximize the sum of entered priority points,
then minimize budget used, then minimize the number of ideas, then choose
lexicographically by stable idea IDs. Five highest-ranked feasible subsets
and the best feasible subset for each selected count are returned.
The empty subset is permitted when there are no required ideas. No
modeled or observed benefit values are summed. Priority points are a
reviewer's preference, not estimated hours or money. Dependencies and
budget constraints are not a resource-loaded calendar or a guarantee
of delivery feasibility.

The report includes the exact portfolio snapshot and scenario digests,
candidate revisions, prerequisite closure and blockers, evaluated subset
counts, mutually exclusive first rejection reasons, feasible alternatives
and budget use/remaining as decimal strings. An infeasible result is
explicit; no partial solution is presented as optimal. Required-closure
cost is only complete if all closure entries are eligible and known.

## API

Dashboard session required; responses no-store. No external calls.

- GET /v1/automation-pipeline/budget-scenario/inventory
- POST /v1/automation-pipeline/budget-scenario

POST accepts a strict JSON object of up to 16 KiB:

```json
{
  "snapshot_sha256": "...",
  "currency": "SEK",
  "budget_basis": "estimate",
  "budget_limit": 100000,
  "max_selected": 5,
  "candidates": [
    {"idea_id": "idea_0123456789abcdef", "revision": 2,
     "priority": 80, "required": false}
  ]
}
```

Use the current inventory digest and actual candidate IDs/revisions.
Any changed portfolio snapshot is refused; review it again. Results are
point-in-time local reads, not continuous monitoring. Export is explicit.
There is no currency conversion, savings claim, new persistent store,
or change to existing planning/transition rules. Tests cover exact
optimization against an independent exhaustive subset oracle, dependency
closure, decimal arithmetic, stale snapshots, and authorization.
