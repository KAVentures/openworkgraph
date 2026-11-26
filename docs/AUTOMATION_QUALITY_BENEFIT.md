# Quality, adoption and conditional benefit review (R16)

Open an implemented idea in the local automation pipeline. Under **Realized
value**, select **Review quality, adoption and cost**. Choose existing
baseline and after measurements, and enter all supplemental counts and
costs explicitly. Unselected periods are disclosed in the report.

## Comparable periods and denominators

The existing measurements supply dates, case counts and retained effort.
The review uses a single SQLite read snapshot of the idea revision and
raw measurement records. A change to the retained snapshot refuses a
previously prepared scenario. Selected periods must be nonoverlapping
within each cohort, entirely before or after the recorded implementation
date, and have positive case counts.

For each selected period, declare:

- Eligible cases, cases with an automation attempt, and attempted cases
  handed to a human. Counts refer to cases, not attempts or tool calls.
- Cases checked using consistent quality criteria, and checked cases
  failing those criteria. Failures cannot exceed checked cases.
- Extra review hours. If review is already included in retained effort,
  extra hours must be zero; otherwise enter only additional review effort.
- Nonlabor operating cost over the measurement period, using a single
  currency across all periods.

No denominator is inferred from missing observations. An empty
denominator produces unknown, not zero. Zero additional cost or effort
must be entered explicitly. Non-handoff does not establish successful
autonomous completion.

## Pooled comparison

Pool counts and hours across selected periods. Compare quality failure
among checked cases, coverage among all cases, attempted adoption among
eligible cases, and handoff among attempted cases. Quality checks may
be selective; these rates do not establish an unbiased population estimate.
There is no statistical confidence interval.

Reviewers declare minimum quality coverage for each cohort, minimum
after-period attempted adoption, and maximum permitted increase in
quality failure rate. Unchecked or insufficiently checked cohorts cannot
pass the quality change gate. Unknown adoption does not pass. A passing
gate is a comparison against entered thresholds, not an independent
approval or policy decision.

Normalize the after case volume to a 30.44-day month. Calculate:

- retained effort difference per case multiplied by after volume;
- effort difference including only additional review hours;
- baseline minus after nonlabor cost per case at after volume;
- conditional net monthly value = adjusted effort change multiplied by
  entered loaded labor cost, plus nonlabor cost change;
- simple modeled payback = entered one-time cost divided by a positive
  modeled net monthly value; otherwise no positive modeled payback.

Quality is a separate gate, not monetized, and a positive financial
scenario can coexist with failed quality gates. There is no currency
conversion. Labor capacity value is not realized cash savings; no causal
effect, actual return, or independently verified business outcome is
asserted. Financial values are display-rounded; extremely large finite
payback values outside JSON float range use a decimal string.

## API and data handling

Local dashboard session only; responses no-store:

- GET /v1/automation-pipeline/{idea_id}/quality-benefit/inventory
- POST /v1/automation-pipeline/{idea_id}/quality-benefit

POST requires the inventory snapshot_sha256, currency, labor_cost_per_hour,
one_time_cost, minimum_quality_coverage, minimum_after_adoption,
maximum_quality_failure_rate_increase, and periods. The three gate
thresholds are fractions from zero to one. Period entries contain
measurement_id, eligible_cases, automation_attempted_cases,
automation_handoff_cases, quality_checked_cases, quality_failed_cases,
extra_review_hours, review_effort_basis (already_in_retained_effort or
additional_to_retained_effort), and nonlabor_cost. Strict JSON body limit:
64 KiB; at most 48 measurement entries.

The report includes selected and excluded measurement IDs, entered
assumptions, pooled evidence, snapshot and scenario digests, review gates
and conditional financial arithmetic. The inventory omits retained source
references and notes. Supplemental inputs are not persisted in OWG or
sent to a provider. Explicit JSON download creates a separate user
controlled copy; it contains entered counts and financial assumptions.
No change to existing measurement, pipeline or approval semantics.

## Whole-population quality and evidence resolution

The report also includes `whole_population_quality`. Without assuming that
checked cases were randomly sampled, the all-case failure count lies between
known failures and known failures plus unchecked cases. Subtract opposite
baseline and after extrema for the exact possible failure-rate difference.
The declared threshold is established within when the upper bound is at or
below it, established above when the lower bound exceeds it, otherwise
unresolved. Comparisons use exact fractions before display rounding. This
is separate from the existing checked-case quality/coverage gates; passing
those gates does not establish whole-population quality.

From the marginal totals alone, the number of cases both attempted without
handoff and passing quality is bounded below by max(0, nonhandoff + known
passes - total) and above by min(nonhandoff, total - known failures). The
eligible-share denominator is explicit; zero eligible cases give null. This
is not case linkage or evidence of autonomous completion.

The report gives a minimum number of additional checks under the most
favorable possible outcomes to establish the threshold, or under the most
adverse possible outcomes to establish a breach. A newly checked after-pass
or baseline-fail reduces the upper difference bound; after-fail or
baseline-pass increases the lower bound. Each step is inverse cohort size;
the exact minimum uses available steps of largest magnitude first. Null
means unattainable even with every remaining check in that direction.
These are best-case arithmetic diagnostics, not instructions on which cases
to sample, confidence intervals, or guaranteed evidence resolution. No
quality outcome is imputed, no new information retained, and financial
arithmetic remains separate. Regression includes exhaustive small-population
integer oracles and strict threshold boundary checks.
