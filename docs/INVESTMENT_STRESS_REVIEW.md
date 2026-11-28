# Common-scenario investment stress review (R16)

In Evidence → Automation opportunity pipeline, select an implemented idea and
run the existing phased investment comparison using exact retained before/after
measurements. Expand **Stress-test cash flows across common scenarios** in its
result. Add 1–12 named scenarios and enter every factor. The unmodified
reference is always evaluated as well.

- Volume and nonlabor cash-change factors range from 0 to 3. Volume is a
  hypothetical fractional planning quantity, not a counted case. Nonlabor
  scales both positive and negative per-case changes.
- Retained effort-effect and cash-conversion factors range from 0 to 1:
  multiplying the existing design assumptions by these factors cannot increase
  the original effect or conversion share.
- Fixed, initial and phase investment cost factors range from 0 to 3; they
  do not scale additional labor cost, which retains the original hourly value.
- Monthly discount rate is a fraction from 0 to 1, replacing the reference
  rate. All factors (and the original discount rate for this review) use at
  most eight decimal places. An optional peak undiscounted cash funding limit
  uses the original currency; no currency conversion is performed.

Review the exact inputs and confirm. All designs are evaluated under the same
finite assumptions, on the same exact pooled effort basis and retained snapshot.
A stale snapshot refuses the report. The output shows each design's worst and
best cash NPV, peak funding, cash regret, scenario-specific cash/economic NPV,
closing cash and sustained break-even within the horizon. Passing designs must
have nonnegative cash NPV in every declared scenario and meet the funding
limit in every scenario if supplied. The minimax regret reference includes
all tested designs (even those failing funding); the displayed minimax and
maximin selections are restricted to passing designs. Ties are retained.
Robust dominance requires no lower cash NPV and no higher peak funding in
every scenario, with at least one strict improvement.

The calculation uses rational arithmetic; exact numerator/denominator and
rounded decimal display are exported. There is no probability distribution,
extrapolated worst case, approved budget, verified savings or causal effect.
The existing investment report and individual cash-conversion sensitivity
remain unchanged. No new retained store, remote provider call or execution.
