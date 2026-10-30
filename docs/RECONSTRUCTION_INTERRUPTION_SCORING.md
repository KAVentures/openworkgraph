# Reconstruction interruption rejection: contradictory assignments

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326)
and [#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264).

The preregistered `interruption_rejection >= 0.95` gate measures the fraction
of **known unrelated interruption event IDs** that a reconstruction explicitly
rejects. For each truth-noise event, rejection now requires **both**:

1. The event ID appears in the prediction's `unassigned_event_ids`.
2. The event ID does **not** appear in any predicted workflow's `event_ids`.

A predicted workflow assignment overrides a contradictory unassigned label for
this metric: the same event cannot simultaneously count as assigned work and
as successfully rejected noise. Merely omitting an interruption from all lists
also does not earn rejection credit. When the truth contains no noise events,
the existing vacuous value of `1.0` remains unchanged.

The per-case scorer now also reports two additive diagnostic counts:

- `assigned_and_unassigned_event_id_count`: distinct IDs present in both
  a predicted workflow and the prediction's unassigned list.
- `noise_assigned_event_count`: distinct truth-noise IDs assigned to at least
  one predicted workflow, whether or not they are also marked unassigned.

The historical keys, metric denominators, family-macro aggregation,
preregistered thresholds and uncertainty handling remain unchanged. These
counts are diagnostics; they are **not** extra release gates. Unknown IDs
continue to reduce assignment precision and are counted separately.

## Synthetic regression

`tests/test_reconstruction_scoring_conflicts.py` uses a new, tiny,
independently specified three-event example rather than opening or modifying
any existing holdout answer keys. It covers a correctly rejected interruption,
a contradictory assigned/unassigned interruption (which must fail the gate),
assigned noise without an unassigned label, a non-noise conflict, and an
omitted interruption. Run:

```bash
python -m pytest -q tests/test_reconstruction_scoring_conflicts.py tests/test_reconstruction_eval.py
```

This fixes **measurement integrity**, not reconstruction/linker quality. It
does not increase held-out assignment F1, establish the 0.90 F1 or 0.05
contamination thresholds, validate a semantic linker, or constitute a real
consenting pilot. Retain family-level holdout separation and do not tune on
holdout answers.
