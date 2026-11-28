# R15 — Exact portfolio frontier and budget sensitivity

From Automation pipeline → Plan a budget-constrained portfolio, select
the same exact current opportunity revisions, single currency/budget
basis, candidate count and priorities as the existing planner. Enable
**Include exact budget frontier and candidate sensitivity**, enter
one to twelve integer priority targets (0–1800) and optionally up
to twelve distinct nonnegative budget checkpoints.

## Exact calculations

The existing bounded 18-candidate subset search runs once. The
extension observes subsets after required membership, blockers,
dependency closure and maximum-count checks, but before rejecting
costs above the current cap. All costs use the existing exact
decimal context; no exchange rates or summed benefits.

For each possible integer total priority (0–1800) retain its minimum
cost, breaking ties by fewer selected ideas and stable lexicographic
IDs. Drop a point if a higher-priority point costs no more. The
remaining complete nondominated cost/priority frontier is ordered by
increasing cost and priority. It represents the full structural
candidate space, not just the current budget. Each point has one
deterministic representative; other portfolios may share that point.

For every requested priority target, select the least-cost subset
meeting or exceeding it, using higher priority, fewer ideas and
stable IDs as tie-breaks. Report additional cost beyond the current
cap, or explicitly infeasible under structural constraints.

For each budget checkpoint, report its highest ranked feasible
portfolio, unused budget, priority difference versus the current
budget and the next strictly higher frontier breakpoint, including
the extra budget needed. If no portfolio is feasible, the first
frontier point is the next breakpoint. If no higher point exists,
the next breakpoint is null. A representative packet's
within_current_budget and additional_budget_required always refer
to the original cap, not the alternative checkpoint.

## Candidate-level sensitivity

At the original budget, for each candidate report exact counts
of feasible subsets including/excluding it; best forced-in and
forced-out portfolios, loss of priority versus the ranked optimum
and signed cost difference. If a candidate cannot be required or
excluded, its constrained optimum is null, not zero. Across
all structural subsets independently report feasible membership
counts and least-cost forced-in/out alternatives; the extra budget
shows whether spending alone could enable that membership.
Required prerequisite closure still applies to every subset.

Count all feasible subsets attaining maximum priority before the
cost/count/ID tie-breaks. Report whether a candidate occurs in all,
some or none of those maximum-priority subsets, and minimum and
maximum cost of those subsets. Equal priority does not imply
equal cost, identical portfolio or independent benefit.

## API and implementation

Existing dashboard-only POST /v1/automation-pipeline/budget-scenario
accepts optional frontier_review:

    {"priority_targets":[50,100,200],"budget_checkpoints":[10000,50000]}

All other request fields are unchanged. Strict request size and
candidate bounds remain. The extra computation runs in the same
enumeration; a single-process nonblocking admission guard rejects
overlapping local portfolio searches. The exact current portfolio
digest is rechecked after the solve. No extra storage or state
changes are performed. The dashboard pages frontier points locally
and downloads the complete report on explicit request. Editing
assumptions invalidates the displayed report. A saved export is
a point-in-time planning artifact, not proof of current conditions.

The solver does not reserve budgets, grant approval, estimate actual
savings, certify delivery feasibility, or execute work. Structurally
blocked candidates do not become viable solely because more funds
are available. Priorities are declared additive preference points.

## Verification

New tests compare 160 independently enumerated small populations
against direct pairwise nondominance, target and budget optimum
oracles, candidate forced membership, maximum-priority tie sets,
and exact decimal costs. Real retained pipeline tests cover
dependencies, infeasible caps, stale snapshots and admission recovery.
Authenticated HTTP and real browser tests exercise the opt-in
workbench and complete JSON export. Native CI remains required.

## Competing stakeholder preferences

Optionally declare up to three alternate priority profiles (at most 16 candidates for this mode, 65,536 subsets). Each profile must contain exactly one integer score 0–100 per selected idea and a distinct label. The original points form the first profile. The template button prepares an editable alternate profile using selected IDs. All profiles use the same current budget, prerequisites, required membership and feasible subsets. The engine retains the bounded feasible score vectors during the original enumeration, then finds each profile's optimum and exactly minimizes worst regret (gap from that profile's optimum), then total regret, cost, count and IDs. A second rule maximizes worst score, then total score, then minimizes cost/count/IDs. The output shows each score and regret, the number of portfolios simultaneously optimal for every profile, and exact alternate inputs. These are declared comparable preference scales, not predictions or measured benefits. The additional tests use 120 independently enumerated small populations and a conflicting-preference compromise case.
