# Deadline-first opportunity scheduling (R15)

After evaluating the existing resource-constrained implementation schedules,
expand **Optimize declared deadlines**. The browser reuses the already reviewed
report, including the exact selected tasks, unfinished prerequisites, durations,
release days and capacity designs. Declare at least one deadline in the
original planner, then explicitly confirm weights (integers 1–1000).

The objective is lexicographic: minimize weighted missed deadlines first,
then weighted late days, then makespan. A weight counts once for a missed
deadline and multiplies each late day. This is not a weighted sum combining
days with misses. Un-dated tasks carry no deadline penalty but participate
in resource and dependency constraints.

The original schedule is independently checked for capacity, timing and
prerequisites and retained as an incumbent. Four deterministic serial
priorities produce alternatives. For at most seven selected tasks, complete
topological serial-order search proves optimality; a matched analytical bound
also proves optimality at larger sizes. Otherwise the result is a feasible
heuristic with a lexicographic lower bound, not a component-wise optimality
gap. The bound uses resource-free earliest completion and pool work.
For a regular objective, placing tasks in nondecreasing start-time order
and moving each left to its earliest feasible slot cannot increase any
completion time. Therefore the exact search covers an optimum.

Compare the original and new objective, capacity alternatives, proof status,
deadline feasibility and exact schedule witnesses. Changes to weights invalidate
the previous export and require confirmation. Keep the original schedule
report alongside the metadata review JSON for reproduction. Its digest is
retained, not independently authenticated or refreshed. The review runs
locally in the browser, changes no plan, authorizes no execution, and does
not verify actual staffing, calendar dates or delivery commitments.
