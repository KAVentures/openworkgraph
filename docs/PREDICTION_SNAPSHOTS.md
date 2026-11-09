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
