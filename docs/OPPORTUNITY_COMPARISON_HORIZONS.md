# Implementation alternative horizons and break-even (Stage 4)

OWG's existing **Process mining → Process improvement signals → Compare
implementation alternatives** feature compares eliminating a step, deterministic
automation, an AI agent, an AI agent with per-case human approval, and keeping
the work human-only. This addition makes the comparison economically more
useful by accounting explicitly for one-time implementation costs, not merely
monthly estimated labor value. All results are local and assumption-driven.

## User journey

1. Import or preview an authorized CSV process log in **Process mining**.
   The log can surface repeated events and long observed transition gaps;
   it **cannot** tell you how many labor minutes were saved or what automation
   should be built.
2. Enter monthly case volume, loaded hourly labor cost, and a separate set
   of eligible-case percentage, minutes saved, recurring cost and one-time
   implementation cost for *each* implementation alternative.
3. For agent-with-approval, enter its required human review minutes, which
   reduce gross labor savings. That option is not an autonomous business
   action.
4. Adjust **Labor impact sensitivity ± (%)** between 0% and 100% (default
   25%). This changes gross modeled labor impact, including negative impacts,
   but leaves recurring and implementation costs unchanged. It is an
   **arithmetic stress assumption**, *not* a confidence interval.
5. Click **Compare alternatives** to inspect annual and three-year net
   value after implementation cost, modeled year-one low/high scenarios, and
   the minimum eligibility fraction needed to break even within 12 months.
   Any change to an assumption clears the previous comparison; rerun it
   to make the figures current.
6. If you explicitly review your scenario, the existing browser-local export
   includes these additional assumed fields without any server write or
   approved-agent action.

## Calculations

For one alternative, with eligible share `p` (0–100%),
`V` assumed cases/month, `T` **net** minutes saved per eligible case
(*gross minutes saved minus human review minutes*), loaded hourly labor cost
`H`, recurring monthly cost `C`, and one-time implementation cost `I`:

```
modeled monthly gross labor impact = V × p / 100 × T / 60 × H
modeled monthly net                = gross impact − C
modeled year-1 value after upfront = 12 × monthly net − I
modeled 36-month value after upfront = 36 × monthly net − I

Year-1 sensitivity low  = 12 × (min(gross × (1−s), gross × (1+s)) − C) − I
Year-1 sensitivity high = 12 × (max(gross × (1−s), gross × (1+s)) − C) − I
where s = entered sensitivity percent / 100.

12-month eligible share required at same volume and positive T,H:
  100 × (C + I/12) / (V × T / 60 × H)
```

A 12-month break-even threshold is shown only if the model can reach it
with an eligible share of at most 100%. Otherwise the UI says
**Not reachable under assumptions**, rather than inventing a ratio for
negative net minutes, zero labor cost, or implementation/recurring costs
too high for the modeled volume. A zero-cost option with positive labor
value can break even at 0% eligibility in this simple definition.

The baseline human-only alternative has zero incremental value and cost,
and no invented break-even fraction. Values are undiscounted, in the
selected currency, and rounded to two decimals at presentation boundaries.

## Interpretation and safety

- **An alternative with the highest monthly modeled net value need not
  have the highest 12-month value** after upfront implementation costs.
  The existing monthly ordering remains unchanged and clearly labeled.
- A negative 12-month value is meaningful; do not clamp it to zero or call
  it successful payback. In a human-approval workflow, more review time
  than saved time can produce a negative gross labor contribution.
- The sensitivity range is **not** a standard error, confidence interval,
  probability, formal forecast, guaranteed return or realized cash flow.
  It assumes unchanged volumes, eligibility, rates and other costs.
- The figures exclude discount rates, taxes, learning curves, training
  expenses, product quality, safety, adoption friction, regulatory work,
  outage risk, changes in customer demand and all unmodeled costs.
- This does **not** authorize process elimination, agent execution, business
  writes or transfer of observed information to an AI provider. All
  alternative-specific assumptions are entered by the analyst; event
  durations are never reclassified as labor savings.
- Source process files and their assumed value reports stay local. No new
  cloud data flows or capture/consent settings are introduced.

## Validation

`tests/test_opportunity_alternatives.py` checks numeric 12-/36-month
values, low/high extremes including negative labor savings, adjustable
sensitivity bounds, impossible break-even refusal, compatibility with
existing alternatives and permission flags. Real Chromium acceptance in
`tests/browser/opportunity_alternatives.cjs` covers importing a sample
CSV, setting independent alternative assumptions, calculating modeled
horizons and selected eligibility thresholds, changing sensitivity,
invalidating stale reports, mobile width and **zero source writes**.

The full CI matrix must pass on the exact PR head and combined main
before merging. These outputs strengthen the opportunity review workflow,
not a claim of complete business-value verification or customer pilot
success.
