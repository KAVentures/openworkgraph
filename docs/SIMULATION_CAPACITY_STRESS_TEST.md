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
