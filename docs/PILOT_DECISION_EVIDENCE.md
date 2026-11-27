# Prospective multi-team pilot decision evidence

R25 / #326 E / #370. Follow-up to the local workspace #1547 and the
prospective pilot design #1545–1546. This is a private evaluation tool,
not a new production runtime, automated deployment gate, or proof of
OWG's commercial effectiveness.

## Why this exists

The canonical product-value evaluator compares control (business tools),
raw evidence and goal-oriented OWG context. The prospective pilot already
freezes a roster and counterbalanced execution order, records attempt
dispositions and supports blinded review. This addition binds a separate
decision protocol to the initial accepted workspace head, then checks
whether observed differences depend on one large or unusual team. It
does not replace the canonical scorer.

## Operator workflow

Use the existing local pilot workspace to initialize the frozen plan and
schedule. Before any mutation, specify the complete decision policy in
a JSON file. The following is an illustrative configuration, **not an
evidence-based recommendation or a universal product launch threshold**.

```json
{
  "min_enrolled": 30,
  "min_teams": 3,
  "min_episodes_per_team": 5,
  "min_goal_success_bps": 8000,
  "max_goal_wrong_action_bps": 500,
  "min_goal_success_delta_bps": 500,
  "max_goal_wrong_action_delta_bps": 0,
  "min_leave_one_team_success_delta_bps": 0,
  "max_leave_one_team_wrong_action_delta_bps": 0
}
```

All nine fields are required. Rate thresholds use integer basis points:
500 is five percentage points. Delta thresholds apply to goal versus
control **and** goal versus raw. The three sample-size minima are
inclusive. At least two teams are required; the operator may demand more.
No threshold is silently selected by the system.

Freeze with the independently retained initial accepted-head pin:

```sh
python -m evals.pilot_decision freeze --db study.db \
  --head-sha256 "$INITIAL_HEAD" --thresholds thresholds.json \
  --out protocol.json --confirm
```

Store the protocol SHA-256 independently before execution. The command
refuses a workspace that already contains a mutation or an attempt.
This does not independently timestamp the file or prevent an operator
from recreating an earlier workspace; custody of the external pin is
essential. Freeze does not modify the workspace.

Run the counterbalanced pilot, use the existing independent blinded
review procedures, and reconcile with the actual truth and registered
review batches. Generate the minimized decision evidence at the exact
current accepted head:

```sh
python -m evals.pilot_decision report --db study.db \
  --head-sha256 "$CURRENT_HEAD" --protocol protocol.json \
  --protocol-sha256 "$PROTOCOL_PIN" --episodes episodes.jsonl \
  --batches reviewed-batches.json --out decision.json --confirm
```

An interim report is allowed: all enrolled episodes remain in the
denominator. Missing or withdrawn outcomes stay unknown. A partial
review cannot meet all decision thresholds even if every numerical
rate bound is favorable. The canonical scorer is invoked only on full
paired coverage as before. The separate decision outcome does not assert
that the canonical scorer's own criteria passed.

Verify the report against an independently retained decision pin and
generate a static local review artifact:

```sh
python -m evals.pilot_decision verify --report decision.json \
  --decision-sha256 "$DECISION_PIN" --out verified.json --confirm
python -m evals.pilot_decision_html --report decision.json \
  --decision-sha256 "$DECISION_PIN" --out decision.html --confirm
```

All outputs are exclusive new files. The HTML has no script, external
resources or network connection. Its fixed tables show outcomes, gates,
policy and provenance, and round exact fractions outwards to two decimal
percentage places. Retain the JSON as the exact numerical record.
Neither file is a release authorization.

## Sensitivity calculations

For an outcome indicator Y and enrolled episode i, an unreviewed outcome
has range [0,1]. For comparison right versus left, the per-episode
difference is in [right_lower - left_upper, right_upper - left_lower].
The sum over episodes divided by the full enrolled denominator gives
sharp marginal finite-population bounds under unrestricted missing
outcomes. The independent canonical coverage result is checked against
these bounds for all arms and comparisons.

For equal-team weighting, divide each team's difference sum by the full
enrolled size of that team and take the unweighted mean of team bounds.
The leave-one-team-out view computes the same episode-weighted contrast
after omitting each team. The displayed envelope covers the extrema of
all removal views. For a minimum robustness gate, take the minimum of
the removal lower endpoints and minimum of the removal upper endpoints;
for a maximum robustness gate, use both maxima. This is the bound on
the worst removal, not the overall display envelope. A single known
failing removal must fail the universal removal gate. A one-team cohort
has no meaningful removal view and fails the team minimum.

A minimum gate passes only when the lower endpoint meets the threshold;
it fails only if the upper endpoint is below the threshold. A maximum
gate passes only when the upper endpoint meets the threshold; it fails
only if the lower endpoint exceeds the threshold. Otherwise the gate is
indeterminate. Full paired coverage is a separate mandatory gate. The
overall result is fail if any gate fails, otherwise indeterminate if
any gate is indeterminate, otherwise pass. Calculations use exact
rational arithmetic, not rounded floating-point comparisons.

All three arm contrasts and both outcome indicators are displayed. Gate
decisions address goal vs both comparators, the absolute goal-arm rates,
the team support thresholds, and all-removal stress tests. These are
descriptive robustness and identification bounds over the declared
population. They are **not confidence intervals**, cluster-robust
statistical estimates, causal effects, or forecasts for another company.
Large differences may reflect selection, source variation, review
error, different tasks or other uncontrolled factors. A prospective
external protocol does not authenticate reviewers or transport receipts.

## Evidence integrity and privacy

The new decision report uses only observations already validated by the
canonical cohort score. A private internal observation callback does
not change existing report formats. The report includes exact plan,
schedule, initial/current head, protocol, reconciliation, score and
coverage digests. The accepted head is rechecked after reconciliation;
it may change after the report is returned. The report omits episode
IDs, team IDs, model/source identifiers, raw prompts, truth, review
bodies, per-episode worklists and raw transport evidence. Aggregate
small-cohort counts may still be sensitive; separately review disclosure.

The report digest establishes consistency against an independently
retained pin. It cannot certify independent outcomes or authenticity.
No automatic agent action, release, or company-wide policy is authorized.
The actual consenting multi-team pilot and source-confirmed outcomes
remain required by R25.

## Validation

The exact pure calculation file and its test file were reconstructed
locally from the committed Git blobs. Initial testing exposed a
worst-removal gate error (confusing the overall envelope with the worst
removal). Corrected and re-tested. All six pure pytest tests passed,
including 450 randomized cohorts with exhaustive missing-outcome
completion oracles, team weighting, removal envelopes, strict threshold
edges, complete-coverage gating and the 500-episode/500-team bound.

Final tested Git blobs:
- evals/pilot_decision_bounds.py: f209ce598cba500daadb6a1afc5071441ab4361b
- tests/test_pilot_decision_bounds.py: 00def6bfa8f5d26f903f9cb61bf3bc56eb4f7737

Actual SQLite workspace, registered blinded adjudication, stale-head
race, CLI exclusivity and HTML rendering fixtures are registered in the
existing offline pilot CI. Direct local Git checkout remains blocked by
DNS; those integrated fixtures have not executed locally. No real pilot,
participant, external model, or live business provider was exercised.
Land only after the parent workspace stack and integrated tests pass.
