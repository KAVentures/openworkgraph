# Exact portfolio budget sensitivity (R15)

The existing local automation opportunity budget planner now optionally reviews
the full cost–priority frontier and candidate inclusion/exclusion sensitivity.
Select up to 18 opportunities, a single budget currency and basis, explicit
priority points, mandatory ideas, maximum selected count and budget cap.
Enable sensitivity and optionally enter up to 12 distinct integer priority
targets from 0 to 1800.

## Results

- Every non-dominated cost–priority pair within the reviewed cap, with a
  deterministic feasible witness. Cost is exact decimal arithmetic.
- The minimum budget within the cap that can attain each declared priority
  threshold, or an explicit unattainable status.
- Each candidate's feasible inclusion and exclusion counts and membership
  among all feasible subsets, maximum-priority subsets, and minimum-cost
  maximum-priority subsets.
- Best alternatives under forced inclusion and forced exclusion, retaining
  all mandatory ideas, dependency closure, count limits and budget limits.
  Infeasible alternatives do not relax any constraints.
- Priority loss and signed cost difference from the original best alternative.
  Negative cost difference can accompany lost priority.

The frontier is complete only within the reviewed cap; no above-cap
recommendation or extrapolation is supplied. Membership counts are
combinatorial counts, not probabilities or independent measures of value.
Priority points are reviewer-entered, not measured benefits. No savings,
budget approval, agent execution or delivery feasibility is inferred.

## API

The existing authenticated local POST /v1/automation-pipeline/budget-scenario
accepts optional include_sensitivity (strict boolean, default false) and
priority_targets (up to 12 distinct strict integers, requires sensitivity).
The response includes sensitivity only when requested. Legacy results and
scenario digest are unchanged when disabled. The same source snapshot digest
and revision checks apply; no separate store or writes are introduced.

The collector operates during the existing complete subset enumeration.
It retains a bounded best row for each attainable priority, and best
inclusion/exclusion rows and membership counters for each candidate.
It does not retain every feasible subset. At most 1801 distinct integer
priority totals are possible; all 2^18 subsets remain the existing work cap.
The opt-in collector adds per-feasible-subset candidate accounting.

## Tests

Run python -m pytest -q tests/test_automation_budget_scenario.py tests/test_automation_budget_sensitivity.py.
Independent enumeration covers exact decimal costs, dependencies, mandatory
ideas, tied optimum membership, infeasible and empty populations, random
small DAGs, input validation, and legacy response compatibility.
