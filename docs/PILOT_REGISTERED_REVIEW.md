# Registered prospective pilot decision review

R25 (#370, #326 E), follow-up to #1626 and the local pilot workspace #1547.
This optional offline workflow commits one decision protocol to the initial
accepted workspace history, before recording any study execution. It also
retains a bounded, minimized history of human next-step decisions. It is
not a release approval, model runner or proof of customer effectiveness.

## Register before collecting pilot results

Initialize the existing pilot workspace with the independently frozen
cohort and counterbalanced schedule. Prepare the nine explicit thresholds
described in PILOT_DECISION_EVIDENCE.md, then register:

```sh
python -m evals.pilot_registered_review register --db study.db \
  --head-sha256 "$INITIAL_HEAD" --thresholds thresholds.json \
  --out registered.json --confirm
```

This advances the accepted workspace to revision 1. Retain the returned
head and protocol digest outside the database. Registration uses the
existing BEGIN IMMEDIATE transaction and requires the exact initial head;
only one competing registration can win. It cannot be added retroactively
to a workspace that already has any accepted mutation, or replaced by a
different policy. The legacy external-file freeze and unregistered
workspace workflow remain available, with their original limitations.

Continue the ordinary append, batch preparation, retirement, withdrawal
and adjudication steps using the newly accepted head. The existing
decision report refuses a supplied protocol that differs from the durable
registration. It identifies the registered head when present.

## Review and record the next step

```sh
python -m evals.pilot_registered_review preview --db study.db \
  --head-sha256 "$HEAD" --protocol-sha256 "$PROTOCOL" \
  --episodes episodes.jsonl --batches reviewed-batches.json \
  --out decision.json --confirm
```

The preview uses the canonical registered blinded-batch reconciliation
and the existing exact decision bounds. Inspect the complete report
before choosing a next-step disposition and fixed reason.

```sh
python -m evals.pilot_registered_review record --db study.db \
  --head-sha256 "$HEAD" --protocol-sha256 "$PROTOCOL" \
  --episodes episodes.jsonl --batches reviewed-batches.json \
  --decision-sha256 "$DECISION" \
  --disposition collect_more_evidence --reason incomplete_review \
  --out reviewed.json --confirm
```

| Next step | Available reasons |
|---|---|
| collect_more_evidence | incomplete_review, source_or_transport_uncertain, external_validation_required |
| revise_product | thresholds_not_met, source_or_transport_uncertain, external_validation_required |
| continue_external_validation | thresholds_met_followup_required |
| stop_study | operator_stop, thresholds_not_met, source_or_transport_uncertain |

The continuation choice requires full paired coverage and a passing
prospective protocol; a failed threshold reason requires a failed
outcome and the incomplete-review reason requires incomplete coverage.
A passing numerical protocol does not mean the canonical product-value
gate passed, does not independently establish real source outcomes, and
does not authorize rollout. All dispositions are human review records,
not automated instructions or enforcement.

Recording recomputes the entire report from the supplied private
materials, checks the exact independent report pin, and commits the
record under a second exact accepted-head comparison. Any intervening
workspace change aborts the write. Up to 50 records retain only report,
score, coverage and prior evidence head digests, fixed disposition and
reason, and outcome/coverage indicators. No new raw material, reviewer
text, individual outcomes or external receipt bodies are stored.

A subsequent workspace mutation makes the previous review historical.
External review sheets may change independently of the workspace head.
A changed canonical score or explicitly changed disposition can supersede
a current review without a separate unrelated workspace mutation. An
identical score/disposition/reason on a current review is rejected.
The record does not prevent subsequent study activity, even after a
stop disposition: operators retain control of the study.

```sh
python -m evals.pilot_registered_review status --db study.db \
  --head-sha256 "$HEAD" --out inventory.json --confirm
```

Latest review current means its record was the most recent accepted
workspace mutation, not that outside reviewer files were checked.
Retain the external report and independent head/protocol/decision pins.
The history validates that the protocol was incorporated into the
first accepted head, and each review refers to an actual predecessor
head and operation revision. It cannot independently reconstruct every
historical state body, authenticate reviewers, establish real freeze
time or detect full database replacement without an independently
retained pin. The workspace remains a single-device local SQLite
database, not distributed study custody. A committed operation can
precede a failed output write; inspect the current workspace before
retrying. Never distribute unregistered or stale artifacts.

## Acceptance

New integration tests exercise the actual existing workspace and
blinded adjudication, including one-winner concurrent registration,
late registration refusal, legacy compatibility, alternative protocol
refusal, exact report checks, stale review history, retired material,
new external adjudications and corrupted state. Registered in the
offline pilot CI alongside the existing parent tests. The six parent
pure arithmetic tests were rerun locally, with their exact Git blob
hashes matching #1626. Direct full checkout remains blocked by DNS;
the new workspace integration tests have not run locally. No real
participant, model or business provider has been exercised.
