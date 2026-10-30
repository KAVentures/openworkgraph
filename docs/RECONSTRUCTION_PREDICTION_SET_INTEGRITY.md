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

## Model response case identity

The external runner supplies a requested case ID to each model call. A model
response may omit `case_id` (the runner attaches the requested ID), or it may
repeat that exact string. If it supplies a different ID or a non-string ID, the
runner now refuses the response instead of silently overwriting the model's
value. This prevents a wrong-case response from being misattributed to the
requested case in a persisted prediction artifact. No provider request is
introduced by the check, and this does not authenticate model outputs or
attest that the response content actually describes the correct case.

## Resume validation against current blind evidence

When the external runner resumes a prediction JSONL artifact, it now checks
each saved row against the **current selected case's presented event IDs**,
using the same membership validator as a fresh model response. A stale row
that references an event no longer present, assigns one event to multiple
workflows, or has malformed workflow/unassigned ID lists is refused **before**
any new model request. The existing JSONL bytes remain untouched on failure.
Valid rows are read without rewriting their persisted JSON.

This guard uses only blind presented evidence, not hidden ground truth. It
does not prove that the evidence is unchanged when the event ID set is
identical, that the model/revision/prompt matches, or that a valid prediction
is accurate. A run manifest with an authenticated corpus digest would be a
separate improvement. The direct helper retains its older ID-only mode for
callers that do not supply current evidence; the command-line runner always
supplies it.

Synthetic tests cover stale references, duplicated assignments, malformed
membership lists, unchanged valid resume bytes and refusal before a model
request. No real provider or model call is used.

## Within-prediction event membership integrity

The external runner now requires every event ID in a model prediction to be a
nonempty string from the **current selected case's blind evidence**. Each ID
list must be duplicate-free. A predicted workflow's ordered, automation-relevant
and UI-mechanic IDs must be subsets of **that workflow's** `event_ids`, not
merely IDs appearing somewhere in the same case. An event may not appear in
multiple workflows or in both an assigned workflow and
`unassigned_event_ids`.

These checks run on both fresh model responses and resumed prediction rows,
before a resume makes another provider request. They do not require a full
ordering list: a valid partial order remains valid, and optional lists may be
omitted. The existing scoring thresholds, private/held-out truth and
uncertainty policy are unchanged. Invalid outputs fail rather than being
silently coerced or treated as successful reconstruction.

This is **contract and measurement integrity**, not evidence of improved
assignment F1, lower contamination, real-provider compatibility, or real
customer outcomes. A prediction with internally consistent event IDs can
still be semantically wrong or grounded in outdated evidence whose IDs did
not change.
