# Reconstruction prediction-set integrity

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326)
and [#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264).

The blind reconstruction scorer now requires **exactly one prediction per
selected case**. A duplicate prediction is an error, rather than silently
letting the last row replace an earlier prediction. Rows for case IDs outside
the selected evaluation corpus are also errors, rather than being ignored.
Empty or duplicate corpus IDs, missing predictions, malformed prediction rows
and non-string case IDs are rejected. Prediction row order remains irrelevant;
the scored output follows the selected corpus order.

The external runner's `--resume` path also refuses duplicate JSONL case IDs
and IDs outside the currently selected family split or `--max-cases` subset.
It validates the existing file **before** starting new requests. A truncated
or malformed JSONL row is not silently discarded. A failed validation leaves
the existing prediction artifact unchanged and does not submit model calls.
The runner continues to append only missing selected cases on a valid resume.

This protects the declared scoring population and makes accidental reuse of
development/holdout rows detectable. It **does not** authenticate a prediction
file's model, prompt, original revision or fixture contents. Users must still
retain the runner metadata and exact corpus revision, and must not tune
against held-out answers.

## Synthetic regression

`tests/test_reconstruction_prediction_set_integrity.py` uses only tiny
independently specified cases. It checks valid row-order independence,
duplicates, missing and unexpected cases, empty identities, valid resume,
cross-split resume, invalid JSON rows and interrupted JSONL writes.

```bash
python -m pytest -q tests/test_reconstruction_prediction_set_integrity.py tests/test_reconstruction_eval.py
```

This is a **measurement and provenance guard**, not an improvement in
reconstruction assignment F1. The preregistered 0.90 F1, 0.05 contamination
and interruption-rejection gates are unchanged. No provider/model was called
and no real consenting customer pilot or competitor parity is claimed.
