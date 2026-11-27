# Counterbalanced prospective pilot execution and blinded review

This is the operator workflow layered on the prospective cohort ledger
(`docs/PROSPECTIVE_PILOT_COHORT.md`). It supports R25's real multi-team
pilot acceptance without introducing a second scorer, recorder, or agent
runtime. All commands are offline, explicit, and private by default. They
validate declared records and exact content pins, **not** identities,
trusted times, consent, real transport execution, or product effectiveness.

## 1. Preregister the cohort and execution protocol

First prepare the exact cohort and retain the independent plan pin using
the existing `evals.prospective_pilot_cohort prepare` command. Choose
a random 32-byte seed independently of the outcomes, for example through
an operator-controlled random draw. Record the seed, plan pin, schedule
pin, and chosen review protocol before any evaluation runs.

```sh
python -m evals.pilot_execution schedule \
  --plan frozen-plan.json --plan-sha256 "$PLAN_PIN" \
  --seed "$SEED_HEX" --review-protocol dual_adjudication \
  --out schedule.json --confirm
```

Choose `single` or `dual_adjudication` in advance; switching between them
after observing outcomes is refused. Within every declared team/model
stratum, the schedule deterministically orders enrolled episodes by a
seeded SHA-256 rank and uses all six three-arm permutations per complete
block. A partial block uses rotations of cyclic Latin squares; every arm's
position counts differ by at most one. Pair precedence may remain
imbalanced in a partial block. Both planned position and precedence counts
are reported. Counterbalancing is not proof of concealed allocation, no
carryover, random sampling, or equivalent source states.

## 2. Record the declared attempt lifecycle

```sh
python -m evals.pilot_execution initialize \
  --plan frozen-plan.json --plan-sha256 "$PLAN_PIN" \
  --schedule schedule.json --schedule-sha256 "$SCHEDULE_PIN" \
  --out ledger-000.json --confirm
```

Retain each ledger digest through an independent study record. For later
commands use the exact prior ledger and separately retained digest.
Example `actions.json` for starting an episode's scheduled first arm:

```json
[
  {
    "episode_id": "episode-001",
    "arm": "goal",
    "action": "start",
    "declared_at_ms": 1795766400000,
    "session_id": "fresh-session-001",
    "model_id": "model-version-A",
    "source_state_id": "state-001"
  }
]
```

The example arm must be replaced with the **actual scheduled arm**. Use
opaque identifiers, not employee names, source credentials or customer
identifiers. The ledger requires the same model and source-state
declarations as the plan; a session ID cannot recur anywhere in the
retained ledger. Use genuinely fresh model sessions and independently
retain transport evidence. The code only verifies uniqueness of declared
IDs and exact pins.

When a turn completes, record its approved material digest and independent
transport receipt digest, using lowercase SHA-256. The private material
object to hash is exactly:

```json
{
  "episode_id": "episode-001",
  "arm": "goal",
  "session_id": "fresh-session-001",
  "model_id": "model-version-A",
  "source_state_id": "state-001",
  "transport_sha256": "<actual transport receipt digest>",
  "review_text": "Operator-approved condition-neutral response",
  "review_material_approved": true
}
```

Replace the placeholder with the actual receipt digest. Hash this exact
object using the cohort module's `digest` function (compact sorted-key
UTF-8 JSON, no nonfinite values). The completion action has exactly the
four base fields `episode_id`, `arm`, `action: "completed"`,
`declared_at_ms`, plus `material_sha256` and `transport_sha256`.
Do not manufacture receipts. The actual approved text can be retained
separately, subject to consent. A declared completion can remain
unreviewed if appropriate material is unavailable.

Terminal noncompletion actions use only the four base fields, with action
`model_failed`, `source_unavailable`, `withdrawn`, or `other`.
A model failure needs a recorded start. An attempt can be started only
once and become terminal only once. Later scheduled arms require all
earlier arms to be terminal, not necessarily successful. Cross-episode
interleaving is allowed. No automatic retry or imputation is permitted.
Declared timestamps must be nondecreasing across the retained ledger.

```sh
python -m evals.pilot_execution append \
  --plan frozen-plan.json --plan-sha256 "$PLAN_PIN" \
  --schedule schedule.json --schedule-sha256 "$SCHEDULE_PIN" \
  --ledger ledger-000.json --ledger-sha256 "$LEDGER_PIN" \
  --actions actions.json --out ledger-001.json --confirm
```

Each append is validated as a whole; the original artifact is not changed.
The new digest binds the full chain, exact action order and prior scope.
The file is created exclusively, with private permissions where supported.
This is not a distributed lock: two operators holding the same old snapshot
can create distinct successors. Use a single authorized study record as
the source of the accepted head and independently record its new digest.
Do not merge forks silently.

## 3. Monitor progress or stop remaining attempts

Use the same plan, schedule, and current ledger arguments from above,
without the actions argument:

- `progress --out progress.json` provides the full private worklist,
  available next actions, declared states and planned/started/completed/
  terminal-noncompletion counts by arm, position and stratum. Actual
  exposure can be imbalanced even when the schedule is balanced.
- `withdraw-remaining --episode-id episode-001 --declared-at-ms <UTC-ms>
  --out ledger-next.json` stops all remaining nonterminal attempts for
  that episode, in scheduled order. Completed attempts stay recorded;
  exclude their material separately from subsequent review if required.
  This changes only the private ledger; it cannot cancel an external
  running model or revoke access by itself.
- `coverage --out coverage.json` produces full-denominator coverage and
  unknown binary-outcome bounds without opening truth, response, or
  review files. A transport completion is not treated as an outcome.

Retained identifiers and hashes may be linkable. Apply the study's
retention/deletion rules to the ledger itself as well as raw evidence.

## 4. Prepare approved blinded batches

You may batch completed attempts while other episodes are still running.
Each material JSON array contains only exact approved material objects
corresponding to completed attempts. The original prompt must also be
reviewed for condition neutrality and disclosure. The tool does not
automatically sanitize free text.

```sh
python -m evals.pilot_blinded_batches prepare \
  --plan frozen-plan.json --plan-sha256 "$PLAN_PIN" \
  --schedule schedule.json --schedule-sha256 "$SCHEDULE_PIN" \
  --ledger ledger-current.json --ledger-sha256 "$LEDGER_PIN" \
  --episodes episodes.jsonl --materials approved-materials.json \
  --out reviewer-packet.json --private-key private/batch-key.json --confirm
```

Store the independent batch pin from the key. Share only the approved
review packet through the authorized channel; never send the key,
schedule, ledger, source receipt, or truth record to a blinded reviewer.
The review packet contains random opaque review IDs, approved prompt and
response text. No arm labels or private metadata are copied; text itself
can still reveal a condition. A reviewer can generate a null-filled sheet
without private data:

```sh
python -m evals.pilot_blinded_batches template \
  --packet reviewer-packet.json --out review-template.json --confirm
```

Use the existing six product-value review fields: selected resources,
selected agent runs, clarification questions, wrong assumptions,
continuation success, consequential wrong action. Each submitted sheet
uses one declared independent reviewer. Missing review rows stay missing;
do not fill unknown outcomes with false.

## 5. Optional preregistered dual-review protocol

For a dual-adjudication study, send the same approved packet to two
distinct independent reviewers. Each must return a complete sheet for
that batch before adjudication. Generate a disagreement-only packet:

```sh
python -m evals.pilot_blinded_batches prepare-adjudication \
  --packet reviewer-packet.json --first-review first.json \
  --second-review second.json --out disputes.json \
  --private-basis private/adjudication-basis.json --confirm
python -m evals.pilot_blinded_batches resolution-template \
  --packet disputes.json --out resolutions-template.json --confirm
```

The private basis pins the original packet, both sheets and disagreement
packet. Record its independent `adjudication_sha256`. The agreement
report includes the existing exact agreement, Jaccard, count difference
and binary Cohen kappa statistics. These are review agreement statistics,
not independent truth. A third reviewer can resolve some or all disputes;
unresolved disputes remain unknown. Share the disagreement packet only
after review of its contents. It intentionally includes the two proposed
judgments, but not arm metadata or reviewer identities.

## 6. Reconcile all batches against the accepted current ledger

Create a private JSON array of batch objects. In a single-review study,
each entry has `packet`, `key`, `expected_batch_sha256`, and
`reviews` (the submitted rows). Embed the actual JSON objects, not file
paths. In a dual study replace `reviews` with `adjudication` containing
`first_review`, `second_review`, `packet` (disagreement packet),
`basis` (the basis object inside the private basis file),
`expected_adjudication_sha256`, and `resolutions`.
Every batch must follow the frozen review protocol.

```sh
python -m evals.pilot_blinded_batches reconcile \
  --plan frozen-plan.json --plan-sha256 "$PLAN_PIN" \
  --schedule schedule.json --schedule-sha256 "$SCHEDULE_PIN" \
  --ledger ledger-current.json --ledger-sha256 "$LEDGER_PIN" \
  --episodes episodes.jsonl --batches reviewed-batches.json \
  --out reconciled-score.json --confirm
```

Batches created against earlier snapshots remain usable only if the
snapshot is an exact prefix of the accepted current ledger. Every review
entry is recomputed against the completion material pin, prompt truth pin,
and the actual completion revision. Attempts and review IDs cannot be
assigned to multiple included batches. The same batch can be submitted
later with additional reviews/resolutions, using the original independent
pins. A completed attempt with no submitted batch remains
`review_unavailable`; assigned but unreviewed remains `review_pending`.
A started but nonterminal attempt remains unknown.

Optionally pass `--review-dispositions review-dispositions.json` with
completed, unreviewed cells explicitly marked withdrawn, review_pending,
or review_unavailable. A withdrawn cell may not be included in any
submitted batch. If truth or review material has been deleted, do not
recover it simply for scoring; use the material-free coverage command.
The exact full truth roster is required for this scorer bridge.

All reviewed runs are reconstructed from the pinned ledger and the
validated review records, not from caller-supplied success flags. The
prospective cohort scorer withholds the canonical product report until
every enrolled episode has all three reviewed arms. The existing scorer's
real-episode and point-estimate gates remain unchanged. The result is a
private evidence-linked comparison; it does not prove real execution,
independence, efficacy, causation, deployment readiness or full parity.

## Limits and acceptance

Up to 500 episodes, 1,500 attempts, 3,000 ledger actions, 500 actions per
append, and 50 nonoverlapping review batches. Individual input/output
files are capped at 32 MiB; approved prompt/response strings are capped
at 64 KiB each. Split large batches and retain material under an authorized
retention plan. Combined reconciliation JSON must fit the file cap.
Commands perform no network calls, recording, model runs, or source writes.

The regression suite exercises balanced blocked schedules, exact history
replay, duplicate/invalid transitions, withdrawal, material-free coverage,
exact prefix reuse, independent reviewer and adjudicator separation,
disagreement resolution, tampered packet rejection, protocol switching,
and the real existing canonical scorer bridge. The actual consenting
pilot, source receipts, operator access controls, allocation concealment,
fresh session isolation and independent business outcome validation remain
required.
