# R16 — Phased investment business cases

Open an implemented automation idea in the local pipeline. Under Realized
value choose **Compare phased investment business cases**. The workbench
compares explicitly declared multi-month scenarios on one retained effort
basis. It does not create a finance ledger or make a staffing decision.

## Exact retained measurement basis

Choose distinct, positive-case, nonoverlapping baseline and after
measurement periods. They must fall entirely before and on/after the recorded
implementation date respectively. Both cohorts pool reported hours and cases
before calculating hours per case; minutes-based records are converted
exactly. Calendar coverage and declared source-kind sets are disclosed.
The measurement and idea revision fingerprint is checked before and after
evaluation. Selected and excluded measurement IDs are explicit. Source
references and notes are omitted. Source identity, reported effort,
causality, actual cash savings, quality and adoption are not independently
verified.

## Declare scenarios

Use one currency, a common monthly discount rate (0–100%), one declared
labor-hour valuation, and a common horizon of 1–60 months. Compare one to
four named designs, each with a separate month-zero initial cash cost,
cash conversion share (0–100%) and up to 12 explicit contiguous phases.
Every month must belong to exactly one phase; no missing month is implicitly
filled. For each phase declare:

- Monthly case volume (including explicit zero if applicable).
- Fraction of the retained per-case effort difference expected to apply.
- Signed nonlabor cash change per case (positive = cost reduction).
- Recurring fixed cash cost per month.
- One-time cash cost in the phase's first month.

These are scenario assumptions, not predictions of volume or adoption.
There is no inflation, tax, financing, terminal value, currency conversion
or accounting classification inferred.

## Arithmetic and metrics

Let delta be pooled baseline minus after reported hours per case,
v the declared monthly volume, e the effect share and h the entered
labor-hour value. Released hours R=max(delta*v*e,0); extra hours
E=max(-delta*v*e,0). With cash conversion c, the cash-flow contribution
from labor is R*h*c - E*h. The economic contribution is R*h - E*h.
Thus unused released capacity is never treated as cash, and a deterioration
does not become positive capacity value. Additional labor is conservatively
charged at the full declared hour value in both calculations.

Add signed nonlabor change and subtract fixed and phase costs. The initial
cost is at month zero; all subsequent cash flows, including first-month
phase charges, are at month end. Discount month m by (1+r)^m, where r is
the declared monthly rate, not an annual percentage. No implicit annual
conversion. All arithmetic and sign comparisons use exact rational numbers
derived from the JSON values; decimal display strings are rounded only at
the output boundary.

Each design reports discounted cash and economic NPV, undiscounted totals,
released/additional hours, unconverted capacity, peak cumulative funding
requirement, monthly component ledgers and cumulative cash chart. First
nonnegative month and first sustained nonnegative month are separate for
cash and economic values, discounted and undiscounted. A later replacement
cost can reverse an earlier crossing. Month zero is a valid crossing when
initial cash cost is zero. Sustained means through this horizon only.
The first entered design is the explicit comparator; deltas do not imply
a recommended design.

## Cash conversion sensitivity

With all other assumptions fixed, cash NPV is A+cB, where A is zero-conversion
NPV and B is discounted positive released-capacity value. B is nonnegative.
Report zero/full conversion endpoints and each declared sensitivity share
(up to 11, plus exact design conversion and endpoints), with sign evaluated
before rounding. If A is already nonnegative the minimum conversion is
zero; if A+B is negative or B is zero with negative A, break-even is
unattainable; otherwise the exact minimum is -A/B. Its ten-decimal display
is rounded upward so it does not understate the requirement. This is a
conditional arithmetic threshold, not a staffing, layoff or cash-saving
recommendation. No probabilities are assigned to scenarios.

## API and outputs

Dashboard-only authenticated local API, no-store:

- GET /v1/automation-pipeline/{idea_id}/investment-case/inventory
- POST /v1/automation-pipeline/{idea_id}/investment-case

POST body limit 64 KiB; example:

    {
      "snapshot_sha256":"<exact current inventory digest>",
      "measurement_ids":["<baseline measurement ID>","<after measurement ID>"],
      "currency":"SEK",
      "labor_hour_value":100,
      "monthly_discount_rate":0.01,
      "horizon_months":3,
      "conversion_sensitivity":[0,0.5,1],
      "designs":[{
        "label":"Gradual rollout",
        "cash_conversion_share":0.5,
        "initial_cash_cost":1000,
        "phases":[
          {"start_month":1,"end_month":1,"monthly_cases":50,
           "retained_effort_effect_share":0.5,"nonlabor_cash_change_per_case":0,
           "recurring_fixed_cash_cost":100,"one_time_cash_cost_at_phase_start":0},
          {"start_month":2,"end_month":3,"monthly_cases":100,
           "retained_effort_effect_share":1,"nonlabor_cash_change_per_case":0,
           "recurring_fixed_cash_cost":100,"one_time_cash_cost_at_phase_start":0}
        ]
      }]
    }

Prepare and inspect exact JSON before evaluating. Changing input invalidates
the prepared result. Explicit JSON report and monthly CSV downloads are
local, user-controlled files with entered commercial assumptions. CSV design
labels are protected against leading formula characters. No new retained
storage, source calls, agent grants, approval or business execution occurs.
Use the separate existing quality/adoption review before considering
financial assumptions; this projection does not pass its gates implicitly.

## Verification

The new tests include independent randomized monthly cash-flow oracles
(120 bounded designs), hand-calculated discounted and undiscounted values,
cash conversion thresholds, deterioration, cost reversals, strict input,
real retained SQLite measurement lifecycle, stale snapshots and
authenticated HTTP. The existing Chromium realized-value journey now
exercises input review, invalidation, cash/economic comparison, chart and
both downloads. Full native and browser CI remain required.
