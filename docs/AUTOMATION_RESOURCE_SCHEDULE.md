# R15: resource-constrained implementation portfolio planning

The Automation opportunities dashboard now has **Plan resource-constrained
implementation**. Choose 1–30 active opportunities and include every unfinished
prerequisite. Enter a pool identifier, uninterrupted duration in relative planning
days (1–180), required interchangeable resource units (1–50), optional release
day (default zero) and optional deadline day. Enter explicit integer capacities
for exactly the used pools (1–8 pools). Compare baseline with up to three
alternative capacity allocations on the same task and portfolio revision basis.
No estimate of real employee time, team capacity, currency, dates or staffing
is inferred. A held/rejected/missing prerequisite or cycle blocks scheduling;
implemented predecessors are satisfied declarations, not verified completions.

Prepare and inspect the exact input, then evaluate. Results show each relative
start and exclusive finish, prerequisite-ready time, delay after readiness,
resource-free precedence slack, deadline lateness, pool load and utilization.
The comparison lists makespan bounds and capacity sensitivity. A demand larger
than a pool's capacity yields an explicitly infeasible scenario, not a silently
reduced task. Every returned schedule is independently checked for capacity,
duration, release and precedence. Export the JSON locally; no opportunity,
approval, budget, state, schedule, agent or business source is modified.

## Model and optimality

This is a nonpreemptive single-pool resource-constrained project scheduling
model with integer time and fixed demand throughout each task. Resource pools
have constant capacity, no working calendars or skills beyond the declared
pool. Day zero is a relative origin; no dates or business-day rules are implied.
Two tasks can share capacity and a task may start when another finishes.
Makespan is the latest exclusive finish. Deadlines are diagnostics, not hard
constraints or the objective; a deadline miss in one returned schedule does
not prove infeasibility unless even its resource-free earliest completion
misses. Resource-free slack is relative to the resource-free makespan, not
slack in the resource-constrained schedule.

Analytical lower bounds combine the release-aware precedence critical path
and, for each pool, its earliest resource-free start plus ceiling(total
unit-days/capacity). Four deterministic serial priority schedules provide
feasible upper bounds. Matching bounds prove the makespan optimum. Otherwise,
up to eight tasks are solved by exhaustive topological priority order search
with serial earliest-feasible placement, pruning partial schedules whose
makespan cannot improve the incumbent. For any feasible schedule, order tasks
by their original starts; dependencies precede successors because durations
are positive. Inductively the earlier tasks can be placed no later than their
original starts: at the original start of the next task and afterward their
resource occupancy is a subset of the original occupancy. The next task can
therefore fit at its original start or earlier. Some serial priority order
attains an optimum. Pruning preserves strictly improving schedules. Equal
makespan alternatives are not exhaustively ranked by deadline or utilization.

For more than eight tasks, an unmatched bound is explicitly a heuristic gap.
No guarantee of optimality is inferred from a feasible result. Each alternative
reports the interval of possible optimal makespan differences from the
baseline using the two independent lower/upper bounds, or null if either is
infeasible. A scenario's own comparison to itself is exactly zero. The
reported point delta compares the returned feasible schedules, not necessarily
the respective optima.

## API and integrity

Dashboard session only. Responses no-store.

- GET /v1/automation-pipeline/resource-schedule/inventory
- POST /v1/automation-pipeline/resource-schedule

Example (replace ID, revision and digest with exact current inventory values):

    {
      "format": "openworkgraph.portfolio-resource-schedule.v1",
      "snapshot_sha256": "...",
      "tasks": [
        {"idea_id":"idea_0123456789abcdef","revision":2,
         "pool":"engineering","duration_days":5,"units":1,
         "release_day":0,"deadline_day":8}
      ],
      "capacities":{"engineering":1},
      "alternatives":[{"label":"Two resources","capacities":{"engineering":2}}]
    }

Strict JSON, 32 KiB request, max 30 tasks and four designs. Exact search is
limited to eight tasks (at most 40,320 full priority orders per design).
The inventory and scenario use the existing budget-planning snapshot basis.
Stale revisions, unknown fields, duplicate ideas, unused pools and mismatched
alternative pools are refused. The completed analysis rechecks the portfolio
digest before returning. This is a point-in-time consistency check, not a
continuous monitor or a durable plan. A downloaded report may contain
opportunity titles and local identifiers; review before sharing it.

Regression tests include independent brute-force schedules, release bounds,
precedence, shared capacity, infeasible demand, unknown prerequisites,
cycles, exact/heuristic distinction, scenario comparison and dashboard
authorization. Real customer duration/capacity calibration and native browser
acceptance remain separate release gates.
