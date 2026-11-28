# Prospective product-value pilot: cohort and attrition ledger (R25)

A complete-case comparison can conceal failed model turns, lost source access,
withdrawals, or unavailable review. This offline companion to
`evals/product_value.py` freezes the declared cohort and paired source/model
basis before analysis, reports every enrolled episode in the denominator,
and withholds canonical scoring when any arm is missing independent review.

This is an operator tool, not a recorder, experiment runner, consent system,
or trusted timestamp service. It performs no model calls or source reads.
Use a real consenting multi-team pilot and independent review as specified in
roadmap #326/#370; this module does not claim that such a pilot happened.

## 1. Prepare the roster before evaluating

Use the existing continuity episode JSONL format. Each truth row needs a
unique bounded opaque `episode_id` and the existing scoring fields. Keep
the actual episode content in the authorized private study environment.
Create a separate assignment JSON array, for example:

```json
[
  {
    "episode_id": "episode-001",
    "team_id": "team-A",
    "model_id": "model-version-A",
    "source_state_id": "state-001"
  }
]
```

All identifiers must be opaque, 1–128 ASCII letters/digits or the allowed
period, underscore, colon and hyphen characters. Do not use employee names,
customer IDs, emails or identifiable source names. The same declared model and
source state must be used across control, raw and goal arms for each episode.
The cohort is limited to 500 episodes.

```sh
python -m evals.prospective_pilot_cohort prepare \
  --episodes episodes.jsonl --assignments assignments.json \
  --out frozen-plan.json --confirm
```

The plan contains only the assigned identifiers and exact canonical SHA-256
digests of the individual ground-truth records, plus the required arms. It
does not contain prompts or answers. An alternative `freeze --roster` accepts
an array of the assignment records with a separately computed
`truth_sha256` for each one. The canonical form is compact sorted-key JSON,
UTF-8, no nonfinite values.

Record the resulting `plan_sha256` through an independently controlled study
record **before reviewing outcomes**. The tool checks equality to the
separately obtained digest, not a trusted timestamp, the actual timing of
enrollment, or the authenticity of the underlying declarations. Re-freezing
after outcomes are known defeats prospective accounting. The tool will not
overwrite a frozen artifact.

## 2. Start the private collection worklist

```sh
python -m evals.prospective_pilot_cohort template \
  --plan frozen-plan.json --plan-sha256 "$PLAN_PIN" \
  --out dispositions.json --confirm
```

There is exactly one initial `not_started` row per episode and arm. As work
proceeds, classify unreviewed arms with one of:

| Disposition | Meaning |
| --- | --- |
| not_started | No completed evaluation yet |
| withdrawn | No further use of that participant's outcome in this pilot |
| source_unavailable | Required declared source state unavailable |
| model_failed | Model run failed or did not complete |
| review_pending | Completed turn not yet independently reviewed |
| review_unavailable | Required independent review cannot be obtained |
| other | Another reason, documented separately under appropriate access |

Remove a missing-disposition row when its actual reviewed run is submitted.
Never remove the episode from the frozen denominator. An absent observation
becomes `not_reported`, not success or failure. Do not retain raw data after
withdrawal where deletion is required; the counts-only planning workflow can
continue without the deleted truth or run. Even opaque identifiers and hashes
can be linkable; do not share the private ledger by default.

## 3. Inspect a private coverage-only snapshot

`inspect` accepts a JSON array containing missing dispositions and optional
declared reviewed cells. Reviewed cells must contain the episode, arm, frozen
model/source/truth pins, the reviewed-run digest, and exact Boolean
`continuation_success` and `consequential_wrong_action`.

```sh
python -m evals.prospective_pilot_cohort inspect \
  --plan frozen-plan.json --plan-sha256 "$PLAN_PIN" \
  --observations observations.json --out coverage.json --confirm
```

This mode validates structural consistency only, not the contents of the
referenced run. Use the actual scorer bridge below when permitted.

## 4. Score actual submitted reviewed runs

Reuse the existing independent review workflow. Supply the original exact
episode JSONL and each arm's reviewed run JSONL. A run must meet the existing
`product_value._validated_review` requirements; the bridge binds each
submitted record to the plan's model/source/episode pins and derives the
Boolean outcomes from the reviewed record, not from separately declared
flags. Empty JSONL arm files are allowed while collecting a pilot.

```sh
python -m evals.prospective_pilot_cohort score \
  --plan frozen-plan.json --plan-sha256 "$PLAN_PIN" \
  --episodes episodes.jsonl --control control.jsonl \
  --raw raw.jsonl --goal goal.jsonl \
  --dispositions dispositions.json --out prospective-score.json --confirm
```

If consent or required retention rules mean the original truth cannot be
supplied, use coverage-only mode rather than restoring deleted data. When any
arm is incomplete the result explicitly withholds the canonical scorer and
product decision. Once coverage is complete, it calls the existing
`product_value.report` unchanged, preserving its original thresholds,
synthetic/pilot-only distinction, paired statistics and limitations. The
existing scorer's review provenance and independence requirements remain.
Full coverage alone is not proof of product benefit or parity.

## Reading the bounds

For binary success or consequential wrong action, with N enrolled episodes,
k observed positives and m unknown outcomes, the identified interval is
[k/N, (k+m)/N]. For a right-minus-left arm contrast, the lower numerator is
the right arm's minimum minus the left arm's maximum; the upper numerator
reverses those choices. Denominators are always N. These sharp marginal
bounds allow each unknown Boolean to vary freely. They are **not** confidence
intervals, causal estimates, or a joint bound on the scorer's multi-metric
gate. They do not assume that missingness is random.

The coverage result includes counts by arm/disposition and private opaque team,
incomplete cell worklist, exact numerator/denominator bounds, paired-reviewed
counts and digest pins. These private results are not anonymized public
reports; aggregate small groups separately under an explicit disclosure plan.

## Validation and release boundary

Tests cover exact cohort/model/source/ground-truth pins, duplicate and
unplanned episodes, invalid statuses/flags, order-stable coverage, private
exclusive output, strict JSON parsing, full canonical scorer equivalence and
incomplete scorer refusal. An exhaustive small-population oracle enumerates
all possible completions to verify the binary bounds.

This additive CLI requires no dashboard, recorder, gateway, scheduler or
worker branch changes. No real pilot or participant enrollment is performed
by the implementation. Complete blinded review and real consent, source
receipts, representative cohorts and deployment acceptance remain separate
requirements.
