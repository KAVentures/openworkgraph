# Dependency deadline windows (R15)

After evaluating the existing resource-constrained portfolio schedule, expand
**Optimize declared deadlines → Review dependency deadline windows**. This
analysis is independent of the weighted objective. It uses the same reviewed
task durations, release days, selected unfinished prerequisites and declared
deadlines. It does not update the portfolio or authorize implementation.

For each selected opportunity the table gives:

- Earliest start and finish with unlimited resources, respecting release days
  and all selected unfinished prerequisites.
- Latest start that can satisfy every reachable declared deadline, ignoring
  resource contention, and the difference from the earliest start.
- One deterministic controlling deadline and dependency path attaining that
  bound; ties choose the lexicographically smallest deadline identifier.
- A release/prerequisite witness path attaining the earliest-start bound.
- The original start and its margin to the dependency bound for each
  capacity design that has a feasible resource schedule.

The forward pass computes earliest start as the maximum of the release day
and predecessor finishes. The reverse pass takes the minimum of the task's
own deadline minus its duration and each successor's latest start minus
that duration. Missing reachable deadlines are represented as null, not
zero slack. A negative margin in a particular schedule means that start
cannot meet all reachable deadlines even if resources cease to constrain
future work. Positive margin is not a guarantee of movable schedule time:
reservations and fixed successor starts may impose tighter constraints.

An own deadline is impossible under the release/precedence assumptions if
its earliest finish exceeds that deadline. If none do, the earliest-start
schedule satisfies all deadlines with unlimited resources. This is separate
from feasibility or optimality with the declared capacity designs.

The analysis stays in the browser, consumes the existing validated schedule
report, and exports its own JSON with the original snapshot and scenario
pins. It does not authenticate those pins or refresh the source. Keep the
original report to reproduce the result. No forecast, approved commitment,
resource feasibility claim or execution grant is inferred.

## Validation

```sh
node --test tests/js/deadline_windows.test.mjs
```

The independent oracle enumerates paths on 600 randomized DAGs; other tests
exercise inherited deadlines, ties, negative windows, missing deadlines,
cycle refusal and original schedule margins. The existing portfolio browser
acceptance checks the integrated table, local download and preserved
deadline-first review. This is stacked on #1649; do not merge into the
parent. Integrate the parent into main first, then reconcile and validate
the combined current-main tree.
