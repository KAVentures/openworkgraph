# Prospective paired trial design (R26)

Before freezing a procurement trial, estimate whether the available number of
independent paired cases could support a **prespecified one-sided superiority
test** at a chosen significance level and per-comparison power. This offline
design tool is not an evaluator, runtime adapter, selection gate or proof of
realized improvement. It does not read or modify procurement stores.

From the repository root, save an assumptions-only JSON request:

```json
{
  "format": "openworkgraph.procurement-trial-design.v1",
  "alpha_bps": 500,
  "target_power_bps": 8000,
  "comparisons": 1,
  "maximum_cases": 50,
  "scenarios": [
    {"win_bps": 6000, "loss_bps": 1000},
    {"win_bps": 3000, "loss_bps": 1000}
  ]
}
```

```sh
python -m shared.agent_procurement_trial_design design.json
```

A win means candidate passes and baseline fails on the same case; a loss is
the reverse. Both pass and both fail are concordant. Specify probabilities
in basis points; their sum cannot exceed 10000. Comparisons is the number of
prespecified candidate-versus-baseline tests (1–9). The current R26 enrollment
cap is 50; the calculator will not recommend exceeding it. Multiple scenarios
represent alternative assumptions, not independent observed datasets.

For d discordant cases, the critical number of wins is the smallest w for
which the upper Binomial(d, 1/2) tail is at most family alpha / comparisons.
Under declared win, loss and concordance probabilities, power is the sum of
the exact multinomial probabilities for all rejecting outcomes. The report
contains the exact numerator and denominator as decimal integer strings,
rounded-down power basis points for display, exact threshold decisions and
all sample sizes from one to the requested cap. It chooses the smallest
sample size meeting the target in every declared scenario, not an interpolation
or a monotonicity assumption. A null minimum means no feasible size within
the cap, not that superiority is impossible.

For example at 5% family alpha, 80% target power, one comparison and 50
maximum cases, win/loss assumptions of 60%/10% meet the target at 18 cases;
30%/10% reach only 67.85% at 50 and never meet the target within the cap.
The combined example therefore has no feasible size. These are illustrative
design assumptions, not real procurement results.

## Design limitations

- One independent paired case, one prespecified binary outcome, no missing
  outcomes. Repeated agent attempts are not independent cases. Correlated
  cases, informative missingness, subgroup selection, sequential looks and
  post-hoc hypotheses are outside this model.
- Bonferroni controls familywise type-I error under valid individual tests;
  reported power is per comparison, not the probability of every comparison
  succeeding. There is no claim of equivalence or noninferiority.
- This tool accepts no case data, runs no agents, makes no network requests,
  and creates no authorization or acceptance decision. Existing R26 trial
  validation, holdout and safety gates are still required.
- Freeze assumptions and endpoints prospectively with the study protocol;
  don't change them after observing the outcomes.

## Verification

```sh
python -m pytest -q tests/test_agent_procurement_trial_design.py
```

The tests use independent rational binomial-tail calculations and exhaustive
ordered three-outcome sequences through seven cases. They also cover invalid
input, duplicate JSON keys, exact familywise penalty, infeasibility and the
CLI. This is a standalone additive module; no dashboard, production routes,
worker branches or existing procurement code are changed.
