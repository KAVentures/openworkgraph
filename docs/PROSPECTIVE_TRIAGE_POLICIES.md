# R19: prospective capacity-matched triage policy review

In **Activity → Large event logs → Predict open cases → Prospective triage policy
review**, load the retained log inventory and explicitly select a saved prediction
snapshot. A snapshot with at least one valid paired model/naive estimate is
required. Freeze a policy before adding new observations. Score it later after
appending a new authorized event-log export.

## Declare and freeze

Choose the frozen outcome model (if that snapshot has one), or a strict
remaining-time-over threshold. The latter ranks the frozen median remaining
time, not a predicted probability of crossing that threshold. Specify a
maximum number of cases to review, a deterministic tie seed, nonnegative
integer false-positive and false-negative error weights (at least one nonzero),
and minimum subgroup support. Preview the exact allocation. Both arms select
the same number of valid paired cases, at most the requested capacity. The
naive arm uses the snapshot's naive median or base rate. Ties use a shared
SHA-256 order with the declared seed; neither arm uses later outcome labels.

The preview counts both-selected, model-only, baseline-only and neither,
cutoff ties, unsupported open cases and invalid paired predictions. Unsupported
open cases are not silently allocated to either arm. Explicit confirmation
freezes the exact policy and ranking digests in the existing snapshot SQLite
file. Ranking reconstruction binds the immutable source snapshot bytes and
the normalized policy. No duplicate case data is stored. Exact repeat freezes
are idempotent while the snapshot is still eligible. At most 12 policies are
retained per snapshot. Delete a policy by its exact digest; deleting its source
snapshot or log also erases its policies.

Freeze refuses a changed visible observation horizon, raw event count, or
frozen open-prefix membership. A legacy snapshot without event-count metadata
cannot be used. This is a conservative retained-data check, not an independent
attestation that the operator never saw the outcome, that historical source
events were complete, or that a hidden mapping/metadata change was absent.
No retrospective parameter search is used or claimed to be preregistered.

## Score and inspect

The evaluation compares each frozen ranking with the later retained log.
Finished cases provide observed binary outcomes. For remaining time, an open
case is known late only once the visible horizon strictly exceeds the threshold.
Other open cases, missing cases, backfilled pre-snapshot completions and
unavailable labels remain unknown. Unknown cases are never labeled negative.

Each arm reports known confusion counts, unknown selected/unselected counts,
sharp finite-population bounds on each eventual confusion count, precision,
recall when defined, and declared weighted classification error. If recall
can be undefined, it is flagged separately. Bounds are marginal, not a claim
that every reported extremum can occur simultaneously. The paired comparison
uses the *same* label for each case in both arms: the per-case extrema of
model-minus-naive error cost and captured positives are summed. This preserves
the exact dependence between rankings and produces tighter identification
bounds than subtracting independent arm intervals. Counts and cost bounds
are integers; proportions are rounded for display.

Inspect subgroup metrics by frozen latest activity and prediction basis.
Groups below the declared minimum support or beyond the 30-group display
limit are withheld and counted; all eligible cases remain in the overall
analysis. Groups are case characteristics, not rankings of people. Explicit
case evidence browsing supports exact assignments, known/unknown outcomes,
missing/inconsistent observations and excluded paired estimates. Every page
binds the evaluated horizon, retained event count, policy digest and exact
eligible observed labels; changed evidence refuses further pages.

Export the aggregate JSON report explicitly. Case identifiers are not in
the aggregate; subgroup labels and local snapshot/policy IDs may still be
sensitive. Inspect before sharing. Case evidence is shown only in the local
dashboard. No intervention is executed or recommended; abstract error weights
are not costs in money or verified savings. These are identification bounds
conditional on the retained observations, not confidence intervals, forecasts
of future performance, proof of causal effects, or a treatment policy trial.

## API

All routes require an authenticated local dashboard session and return
no-store responses. Base path:

    /v1/event-logs/{log_id}/prediction-snapshots/{snapshot_id}/triage-policies

- GET base: bounded saved policy inventory.
- POST base + /preview: strict policy object.
- POST base: policy object plus expected_policy_sha256 and confirm_freeze.
- GET base + /{policy_id}/evaluation: current aggregate evaluation.
- POST base + /{policy_id}/evidence: expected_evaluation_sha256, filter, offset,
  limit (1–100).
- POST base + /{policy_id}/delete: expected_policy_sha256 and confirm_delete.

Example policy:

    {"label":"Review capacity 20","target":"remaining_time_over",
     "remaining_seconds":86400,"capacity":20,"tie_seed":17,
     "false_positive_cost":1,"false_negative_cost":3,"minimum_group_cases":10}

For frozen_outcome, remaining_seconds must be null. The exact model is the
existing frozen snapshot model, not a newly trained model. Max 5,000 frozen
predictions. No new collection, source permissions, execution authorization,
provider read, scheduled job, or model call is introduced.
