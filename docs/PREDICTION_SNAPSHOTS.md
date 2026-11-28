# Prediction snapshots (R19)

Backtests and [prediction monitoring](PREDICTION_MONITORING.md) replay a log's past. A
**snapshot** is prospective. It freezes today's estimates for every open case, and once
newer events have been appended to the log, it scores those frozen estimates against what
actually happened.

## Take a snapshot

In *Predict open cases*, set the activities that end a case (and, optionally, an outcome),
then choose **Save a snapshot to score later**. The snapshot keeps, for every open case with
enough comparable history:

- the median, 10th and 90th percentile of the remaining time;
- the outcome risk, if an outcome was chosen;
- the single estimate every case would have received (the naive median and base rate).

It also keeps the settings and the log's latest event time.

Snapshots use the completed-cases estimator. A snapshot holds at most 5,000 open cases
(narrow it with a cohort otherwise), and a log keeps at most 50 snapshots. Deleting the log
deletes its snapshots.

## Score it later

After you append a newer export, choose **Score now** on the snapshot. The cases are split
three ways:

- **Finished cases** (now ending with an end activity) are scored:
  - mean and median absolute error of the frozen median remaining time, next to the naive
    estimate's error;
  - mean signed error;
  - how often the actual remaining time fell inside the frozen 10th–90th percentile range
    (nominally 80%), with a 95% Wilson interval;
  - for an outcome, the Brier score against the base rate's, and the observed rate against
    the mean predicted risk.
- **Still-open cases** are censored: all we know is that their remaining time is at least the
  time since the snapshot. Those already past their frozen 90th percentile are counted as known
  misses; the rest are unknown.
- **Cases no longer in the log** are counted separately.

Scoring only finished cases favours short ones, because the long cases are the ones still
open. Read every score next to the share still open.

Remaining time is measured to a case's last recorded event, as for live predictions. Scores
describe this log. They are not commitments, targets or ratings of anyone.

## API (dashboard session only)

| Route | Purpose |
|---|---|
| `POST /v1/event-logs/{log_id}/prediction-snapshots` | Take a snapshot; body as for `/predictions` (completed-cases estimator) |
| `GET /v1/event-logs/{log_id}/prediction-snapshots` | List snapshots |
| `GET /v1/event-logs/{log_id}/prediction-snapshots/{snapshot_id}/evaluation` | Score a snapshot against the log as it is now |
| `POST /v1/event-logs/{log_id}/prediction-snapshots/{snapshot_id}/delete` | Delete a snapshot |


## Full frozen-population coverage and paired quality

The evaluation now includes a separate set of finite-population bounds over
every predicted frozen case, even before any has completed. Known range
membership includes completed cases and open cases that have exceeded their
frozen upper bound. Other open, missing, or inconsistent cases have unknown
membership. The lower coverage bound treats every unknown as outside; the
upper treats every unknown as inside. These are rounded identification bounds
conditional on complete observation through the selected log/cohort horizon,
not statistical confidence intervals. The eventual mean absolute error has a
lower bound: completed absolute errors plus the minimum possible open-case
errors given elapsed time, divided by the full frozen population. Unknown
missing observations contribute zero to the bound, not a claim of zero actual
error. No finite upper error bound is asserted until every case has finished.

A backfilled completion before the snapshot or a regressed observation horizon
is reported separately as inconsistent evidence, not a prospective completion.
Cases excluded by changed cohort membership are counted as missing. These
checks cannot independently certify source completeness, detect all backfills
or prove that case identities are stable.

The paired remaining-time comparison uses only finished cases with both frozen
estimates and reports exact better/worse/tied case counts. The paired outcome
comparison uses cases with both frozen probabilities: for unresolved outcomes,
both possible binary labels are considered, giving bounds on eventual Brier
score and the model-minus-baseline difference. Negative differences favor the
model. Missing paired probabilities are counted separately, and no missing
outcome is imputed. These fields supplement, rather than replace, the original
finished-case scores and Wilson interval. They are not guarantees of future
performance.

Regression coverage includes exhaustive binary-label assignments, paired
baseline gaps, range boundaries, missing observations, an all-open population,
and real retained-log append/backfill integration.

## Prospective model comparison and proper scores

The snapshot evaluation also returns `paired_remaining_time_population`.
For each frozen valid model/naive median pair, the absolute-error difference
is bounded across all possible nonnegative completion times (or times at
least the elapsed observation horizon for a still-open case). This difference
is monotone between the two medians and constant outside; its extrema are
attainable at the censoring boundary or beyond both medians. Thus even with
unfinished cases, the paired mean difference has finite sharp identification
bounds. Negative favors OWG. Better/worse/tied case count bounds are separate
marginal bounds, not necessarily simultaneously attainable. Missing pairs
are excluded and counted. A resolved sign applies only to that frozen paired
population, conditional on complete observation, not to future cases.

`proper_remaining_time_scores` reports pinball loss at the frozen 10th, 50th
and 90th percentiles, and the central 80% interval score (width plus ten times
the distance outside the interval). Lower is better; narrow but inaccurate
intervals are penalized, as are very wide intervals. Completed scores and
whole eligible-population lower bounds are distinct. For still-open cases
the minimum possible penalty increases when the censoring time passes the
prediction or interval upper end. Unknown cases contribute only the minimum
possible score (zero pinball or interval width), not an imputed completion.
Upper bounds remain unknown until all eligible cases are observed. Invalid
estimates and absent pairs are disclosed. No new stored data, threshold
selection, execution authorization, or statistical confidence claim is added.


For censoring-aware quantile and fixed-bin outcome calibration over exact frozen populations, see [Prospective calibration review](PROSPECTIVE_CALIBRATION_REVIEW.md).
