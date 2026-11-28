# Demand-period capacity planning (R14)

Use Evidence → Large event logs → simulation → **Plan staffing changes across
tested demand periods**. Download a capacity-grid report from the existing
capacity or multi-SLA review. The planner uses that single report's original
cycle-time target, complete tested design/rate/seed grid and all-arrival
accounting. It does not need a source log or run another simulation.

1. Choose the exact JSON report (up to 8 MiB); review the tested rates,
   seeds, elapsed-time deadline and file SHA-256. Reports are unsigned.
2. Enter currency, the explicit per-design staffed-hour cost in minor units,
   service share to basis-point precision, optional initial design, a switch
   limit, cost per switch and optional total budget. No values are inferred.
3. Add, remove or reorder 1–32 demand periods; choose only exact tested rates
   and whole-minute durations. Confirm assumptions and calculate.
4. Inspect the minimum-cost witness, exact switch-count cost frontier,
   fixed-design comparators and each period's seed evidence. Clear or edit
   assumptions to invalidate the result. Optionally download minimized JSON.

Every period must use a design whose lower service bound meets the declared
share in every seed and is never overloaded. Completed late and abandoned
arrivals are misses; unresolved arrivals cannot establish a pass. Counts are
rechecked against the full grid, rather than trusting supplied cell statuses
or percentages. The same seed is not an observed matched case.

For a period of M minutes with H minor currency units per staffed hour,
the cost numerator is M×H over 60; a switch costs 60×fee in the numerator.
The dynamic program retains minimum exact integer cost for each last design
and exact switch count. Equal-cost path counts use arbitrary precision.
The frontier enumerates all feasible switch counts, including those outside
the user cap. Among eligible plans within the cap and budget, ties prefer
fewer switches and a deterministic input-order witness; all minimum-cost
ties are counted. The model also distinguishes no eligible period design,
a restrictive switch cap and an insufficient budget.

This is conditional on the independent simulation cells. It does not simulate
a transition between two designs, queue carryover, staffing availability,
real costs, forecast demand, shifts or actual SLA performance. No production
capacity, savings, staffing recommendation, or execution authority is claimed.
A digest identifies the uploaded bytes; the original calibration digest is
reported as declared, not recomputed or authenticated. All inputs/results
remain in memory in the local browser; no network upload or new persistence.

Validation: 600 independent exhaustive-sequence oracles check every exact
switch-count cost, tied optimum counts, budget and selected witness, with
additional edge, large-tie, input rejection and overload cases. Full browser
and native CI required before merging.
