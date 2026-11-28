# Demand and capacity stress testing (roadmap R14)

The local event-log workspace's What-if simulation section now includes a demand-by-design stress grid. Load measured values, choose a measured activity and one to three alternative staffing levels, enter two to eight explicit demand rates, a cycle-time target, minimum on-time share, seeds and cases. Review the generated input JSON before running. The selected working calendar can be reused. The API additionally supports multiple activity overrides, shared pools, priorities, and explicit weighted paths. Grid routing is complete-path only; fixed/exponential service time is supported.

The grid reuses one frozen numeric calibration for all cells. Each cell repeats the same declared number of seeded runs; seeds are shared across designs and loads. Every post-warm-up arrival is classified as completed on time, completed late, abandoned or unresolved. A lower target bound counts only on-time completions; the upper bound adds unresolved arrivals. An overloaded cell is always flagged, even if its finite run met the deadline. A cell meets its target only if all replications are stable and their lower bounds meet the threshold.

The report shows the worst-replication bounds, completed-case p90 range, overload and exact seed-level accounting. The tested operating envelope lists explicit rates meeting the target; it never interpolates, extrapolates, recommends a design or treats the highest passing point as a production capacity estimate. Nonmonotonic patterns are flagged. Download JSON and CSV; the JSON contains exact normalized inputs and calibration fingerprint, and inputs/reports can be imported for a reviewed rerun. A rerun recalibrates against the currently selected log; no source-content attestation is implied.

## API

Dashboard-session-only POST /v1/event-logs/{log_id}/simulation/capacity-grid, optional case_type. Example:

```json
{
  "format": "openworkgraph.simulation-capacity-grid.v1",
  "baseline": {"cases": 400, "seed": 1, "distribution": "fixed", "activity_time_basis": "mean"},
  "designs": [{"label": "Two reviewers", "activities": {"Review": {"resources": 2}}}],
  "arrival_rates_per_hour": [0.5, 1, 2],
  "replications": 3,
  "target": {"cycle_time_minutes": 240, "minimum_completion_share": 0.9}
}
```

Bounds: up to four total designs, eight demand rates, 24 cells, eight replications, 3,000 cases per run, 100,000 simulated cases, four million potential route steps, one concurrent grid per local process, and a 64 KiB input. Calendar grids refuse an expected arrival span above two years. All designs are parsed before any simulation; failure returns no partial grid. Existing what-if and portfolio routes are unchanged.

## Verification limits

Regression coverage exercises a real event-log calibration, deterministic paired runs, known late/abandoned/unresolved accounting, nonmonotonic envelope, strict schemas, workload admission, concurrent grid rejection/recovery, and dashboard-session HTTP authorization. Native CI, browser acceptance and real customer workloads remain separate release gates. Observed gaps can include waiting; neither simulation nor statistical repetition proves actual staffing needs, causal savings or business outcomes.

Paired comparisons at the same demand and seed include exact target-share change bounds (alternative lower minus baseline upper, and alternative upper minus baseline lower). Cycle-time p90 changes are withheld at cell level if any paired run is overloaded, incomplete, abandoned or lacks a quantile. The CSV includes paired-change rows distinguished by status; changes are fractions (multiply by 100 for percentage points), and p90 values in these rows are changes in seconds. Shared seeds do not identify the same observed cases.

Shared-pool staffing must be set at the pool level. The grid refuses an explicit individual resource override on any pooled activity (including an inherited baseline override), before any cell is simulated. Each cell records the effective dedicated staffing and shared-pool configuration, so a reviewed design cannot silently misrepresent an ineffective override.

## Cost-constrained capacity decision review

Enable the optional cost review before preparing a grid. Enter a single
currency code, a default cost per resource-hour, an upper budget per staffed
hour, one positive weight per explicit demand rate, and up to eight service
share thresholds including the grid target. Review the exact JSON. Advanced
users can add exact dedicated-activity and shared-pool cost overrides; unused
or pooled-activity overrides are refused before any simulation. No default
currency or cost is inferred. Example additional field:

```json
"decision_review": {
  "currency": "SEK",
  "default_resource_hour_cost": 300,
  "maximum_staffed_hour_cost": 1500,
  "demand_weights": [
    {"arrival_rate_per_hour": 0.5, "weight": 1},
    {"arrival_rate_per_hour": 1, "weight": 2},
    {"arrival_rate_per_hour": 2, "weight": 1}
  ],
  "service_share_thresholds": [0.8, 0.9, 0.95],
  "dedicated_hour_costs": {"Review": 450},
  "pool_hour_costs": {}
}
```

The same grid runs exactly once. Effective resource costs charge each
dedicated resource and shared-pool resource once per staffed hour. They are
not actual payroll, utilization, calendar-hour costs or verified savings.
Costs use decimal arithmetic, target bounds use exact arrival fractions.
No currency conversion or automatic staffing decision is performed.

Each design reports cost components, budget status, every-rate service
status, worst rate/seed target bounds and declared demand-mix bounds per
shared seed. Normalized weights describe a scenario, not observed demand
probabilities; an excellent mixture cannot override a failed rate. Budget
and service qualification requires every tested rate and seed stable with
lower service bound at or above the threshold. Overloaded, known misses and
unresolved outcomes are distinguished. No imputed completions.

Robust dominance between stable designs requires no higher cost and the
lower service bound at least the other design's upper bound in every matched
rate and seed, with strict advantage in cost or at least one service bound.
The witness reports the matched cells and minimum/maximum lower advantage.
Non-dominated designs are not recommended, nor necessarily feasible. The
threshold sensitivity table reuses the same runs and reports every tested
design qualifying at each threshold, the minimum qualifying tested cost and
all exact ties. It is not a search of untested staffing levels. The full
optional review is included in the normal JSON report/export and can be
replayed from normalized inputs against a new calibration. Existing inputs
without decision_review keep their old report shape. No new durable storage,
provider access or execution authority is introduced.

Regression includes explicit unequal demand weights, exact threshold/cost
boundaries, cost ties, robust pairwise witness, incomplete grid rejection,
shared pools and real retained-log calibration. Native/browser acceptance
and customer-cost validation remain separate gates.

Cost arithmetic uses a private high-precision decimal context covering the
entire accepted finite numeric range, including very small positive rates.
Budget comparisons and dominance cost differences do not depend on the
caller's decimal context or silently round away such costs.


For evaluating multiple service deadlines on the same simulated population,
including inverse deadline bounds, see [Multi-SLA capacity review](SIMULATION_SERVICE_LEVEL_REVIEW.md).
