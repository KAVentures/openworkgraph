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


## Typed uncertainty and workflow identity

The external prediction validator refuses non-boolean values for
`insufficient_evidence` and the supplied
`uncertainty.membership`, `uncertainty.business_rule`, and
`uncertainty.outcome` dimensions. In particular, JSON `"false"` is
**not** interpreted as `true` by Python truthiness, and numeric zero
is not treated as a valid `false`. When supplied, `uncertainty` must
be a JSON object. This prevents malformed model output from silently
altering the preregistered uncertainty metrics. Legacy predictions
omitting the optional uncertainty object or boolean still work; the
omitted `insufficient_evidence` defaults to `false` as before.

Each workflow must have a unique, nonempty string `prediction_id`.
Omitted IDs continue to receive the existing `workflow_N` defaults,
but duplicate, empty, null and numeric IDs fail rather than
creating ambiguous provenance. This validates identifiers, not the
semantic correctness of the inferred workflow.

The same checks apply to newly received predictions and `--resume`
artifacts. An invalid resume fails before requesting additional model
responses and leaves the JSONL artifact unchanged. The synthetic
regressions exercise malformed values, valid legacy omissions and a
no-request resume refusal. They do not constitute a real-model run or
a measured assignment-F1/contamination improvement. The held-out
truth and release thresholds remain unchanged.

## Exact blind-evidence binding for new external-runner rows

Newly generated `run_external.py` JSONL predictions carry the reserved
`_owg_blind_evidence_sha256` field. The runner computes SHA-256 over a
domain-separated, canonical JSON encoding of the **requested case ID and the
complete blind `presented_evidence` array**, retaining array order but
normalizing object-key order. This uses only the material already shown to the
model, **never** the hidden answer key or private evaluation truth. The model
cannot supply or override this reserved field.

On `--resume`, any row containing this field must have a well-formed
lowercase 64-character digest matching the current case's complete blind
evidence. A changed action, resource reference, tab, surface or event ordering
is rejected **even if all event IDs remain identical**. A mismatched or
unverifiable digest stops the run before any new model request and leaves the
existing JSONL bytes unchanged. This check supplements, not replaces, the
existing event-ID membership, cross-case, split and uncertainty validation.

For backward compatibility, older prediction rows without the field remain
loadable and continue to receive the prior event-ID validation, but their
**content is not digest-attested**. The new field is local metadata, ignored by
the existing scorer and does not change any metric or threshold. This is a
deterministic consistency fingerprint, **not a signed authenticity proof**:
a malicious actor with write access could change both evidence and digest.
It does not bind the model ID, prompt, code revision, response provenance or
original provider receipt. Keep metadata and corpus revisions separately.

Synthetic fake-provider tests and offline replay tests exercise newly issued
digests, canonical ordering, changed same-ID evidence, tampering, missing
verification material, legacy compatibility and fail-before-request behavior.
No model endpoint, live provider, private holdout truth or paid API was used.
This closes a replay integrity gap, **not** the outstanding F1 >= 0.90,
contamination <= 0.05 or real consenting pilot gates.
