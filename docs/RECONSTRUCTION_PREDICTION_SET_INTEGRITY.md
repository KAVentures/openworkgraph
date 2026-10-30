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


## Resume metadata and response-receipt continuity

The external runner also checks the prior metadata manifest **before**
appending to an existing prediction JSONL. If a manifest exists, its exact
requested model ID, endpoint base URL, temperature, family split and selected
case count must match the new invocation. A mismatch or malformed prior
response list fails closed before another provider request or any output-file
rewrite. This catches sanitized-model-filename collisions and accidental
cross-endpoint/configuration mixing; it is not proof that a remote endpoint
served a particular model revision.

On a valid resume, prior per-case response receipts are carried forward and
new response receipts are appended. Duplicate, malformed or out-of-population
receipt references are refused rather than silently relabeled. A completed
run's manifest can have missing old receipts (for example from earlier
versions that discarded them): the runner marks
`resume_provenance_unverified: true` and **never invents** missing receipts.
That flag persists across subsequent resumes. New complete runs start with
`false`.

An interrupted first run may have a JSONL prediction file but no manifest,
because the manifest is written only after scoring. Such resumes remain
possible for backward compatibility, but are marked
`resume_provenance_unverified: true`; the original model/endpoint cannot be
verified. Operators needing reproducible model attribution should start a
fresh output directory and retain external provider receipts. The manifest
check does not bind prompt version, model weights, provider identity,
authorization or hidden evaluation truth. It does not change reconstruction
assignments, scores or the preregistered acceptance thresholds.

Synthetic no-network regressions cover mismatched settings (with unchanged
JSONL/metadata and no model request), malformed/duplicate/out-of-population
receipts, preserving old receipts when adding new ones, and explicitly
unattested legacy interrupted resumes. They do not establish real provider
reproducibility, held-out F1 improvement or customer acceptance.

## Crash-safe append boundaries

A partially completed `--resume` with valid JSON on the final line but **no
terminating newline** is now refused before another provider request if any
selected cases remain to be appended. Otherwise append mode would concatenate
the next prediction onto the previous JSON object, corrupting the JSONL after
spending a model call. The original file is unchanged; repair its JSONL
boundary deliberately or use a fresh output directory. A completed final row
without a newline remains readable because nothing needs appending.

A metadata manifest without matching persisted prediction rows (missing,
empty or whitespace-only JSONL) is also rejected rather than silently
overwritten. Legacy interrupted JSONL **without** a manifest remains supported
and explicitly unattested. Synthetic regressions cover no-request,
byte-preservation and completed-row compatibility. This does not establish
multi-file transaction durability, model authenticity, assignment-F1
improvement or real-provider acceptance.

## Selected blind-corpus identity preflight

Before opening an output file or requesting any paid model response,
`run_external.py` now checks the **selected** split (after `--max-cases`)
for nonempty, unique string case IDs and a list of blind evidence objects with
nonempty, unique string `event_id` values **within each case**. Duplicate
event IDs in a single case previously collapsed in the per-request set while
duplicate case IDs could trigger provider requests before the scorer detected
them. Both now fail closed without writing a prediction or metadata artifact.

IDs are case-local: the same event ID in *different* cases is allowed.
Evidence order is preserved; an empty evidence list is still a valid
uncertain/unknown case. This check reads only `case_id` and
`presented_evidence`, never hidden `ground_truth`, and does not merge cases
by timestamp, infer obligations, or change the scorer/acceptance thresholds.

Synthetic tests cover duplicate cases/events, missing/invalid IDs, malformed
evidence, cross-case reused IDs, interleaved event ordering, and a command-level
fail-before-request/fail-before-output guarantee. This is input/measurement
integrity, **not** a demonstrated assignment F1 or contamination improvement,
real-provider validation, or a consenting customer pilot.


## Duplicate JSON object keys fail closed

Python's ordinary JSON parser accepts duplicate keys and silently keeps the last
value. In reconstruction this can hide contradictory `case_id`, nested
`event_ids`, uncertainty flags, blind-evidence digest fields or metadata
receipts. The external runner now rejects duplicate object keys **at any
nesting depth** in model JSON responses, resumed prediction JSONL and existing
resume metadata. It refuses an ambiguous resume before requesting another
model response or changing the saved prediction/metadata bytes.

This is a field-name constraint **within each JSON object**: separate workflow
objects may use the same schema keys, and case-local event IDs in different
cases are still valid. It does not alter the scorer, evidence order, holdout
answers or any assignment/contamination threshold. Synthetic tests cover
root/nested duplicates, a fake model response, and fail-before-request
resume/manifest rejection. No real model or customer pilot was run.

## Finite JSON and request parameters

Python's permissive JSON decoder accepts nonstandard `NaN` and `Infinity`
tokens and can turn overflowing exponent literals (such as `1e999`) into
infinity. The external reconstruction runner now refuses these values in
model JSON, persisted JSONL predictions and prior metadata manifests,
including nested fields, before treating them as evidence or continuing a
resume. Finite JSON decimals remain supported. The runner also refuses
non-finite or negative `--temperature` and non-finite or nonpositive
`--timeout` before reading cases, creating artifacts or making a potentially
billable request. Finite nonnegative temperature bounds remain
provider-specific. These checks do not authenticate the provider, prove
prediction quality or alter any preregistered scoring threshold.


## Pre-request blind evidence serialization gate

The runner now preflights **every selected case's blind presented evidence**
before creating the output directory, opening a prediction artifact or
requesting a model. Nested non-finite numbers, Python-only values (including
tuples), non-string object keys and evidence that cannot be encoded as the
canonical finite JSON used for the local evidence fingerprint are rejected.
Case-indexed errors do not echo the potentially sensitive source payload.
Finite nested JSON values remain valid. The gate also runs for legacy
digestless `--resume` rows, which otherwise would not trigger digest
verification until after a new model request.

The strict digest itself still binds only *blind* evidence and case identity;
it does not authenticate the model, the source, or hidden answer keys. This is
an early refusal of malformed evidence, **not** improved held-out assignment
F1, contamination, interruption rejection, provider validation or customer
pilot proof. Synthetic tests exercise preflight, finite compatibility and
fail-before-request/no-mutation behavior; no paid API was invoked.


## Strict outer provider response envelopes

The model's JSON content was already parsed strictly, but the surrounding
OpenAI-compatible HTTP completion envelope previously used Python's permissive
response.json(). That could silently choose the last of duplicate outer
choices or usage keys, or persist non-finite numbers in usage receipts
(including overflowed JSON exponents) even when the model prediction itself
was valid. The runner now parses the **raw outer response text** with the same
duplicate-key and finite-number checks before accessing choices or writing
prediction/receipt artifacts. A non-object envelope is also refused.

Synthetic fake-provider tests cover root and nested duplicate keys, non-finite
usage and otherwise discarded envelope fields, finite nested receipts, and
prove the permissive response.json() shortcut is not used. This check does
not authenticate a provider, attest usage billing, establish a real API
integration, change the answer key or demonstrate held-out F1 improvement.


## Standalone scorer JSONL ingress

The standalone `python -m evals.reconstruction.scoring` command accepts
prediction JSONL from external producers, not only the validated external
runner. Its JSONL reader now rejects duplicate object keys at any depth,
non-standard `NaN`/`Infinity` constants and numeric exponents that overflow
to infinity, with a line number identifying the offending row. Previously,
Python's permissive JSON decoder could silently keep the last duplicate
value or accept non-finite data. Valid finite JSONL, blank lines and
out-of-order case rows remain supported; malformed files are never
rewritten. These checks run before scoring or writing a report.

The regression fixtures are entirely synthetic and cover conflicting case
IDs, nested workflow and uncertainty keys, non-finite ignored metadata,
positive finite nested metadata and non-object rows. This protects the
integrity of the submitted scoring artifact; it does **not** authenticate
its origin, prove assignment correctness, alter private holdout truth,
change the scorer or thresholds, or demonstrate F1/contamination progress.


## Whole selected blind-corpus binding in completed manifests

New external-runner metadata records "selected_blind_corpus_sha256": a
domain-separated SHA-256 over the **ordered selected cases**, each case ID and
its complete ordered blind "presented_evidence". JSON object-key order is
normalized; case/event-array order is not. Hidden "ground_truth", answers,
private holdout labels and model outputs are **not** included.

When resuming an artifact with this manifest field, the runner compares the
current selected blind corpus before opening the prediction file for append or
making another model request. A same-ID changed record, altered observation,
changed event ordering or changed case ordering fails closed even if legacy
prediction rows lack their per-row blind-evidence digest. The old files remain
untouched on mismatch. New manifests always carry this digest.

Old manifests without the field remain readable for backward compatibility,
but the resulting run is marked "resume_provenance_unverified: true" even when
its prior response receipts are complete. A missing manifest was already
unattested. A malformed or mismatching **present** digest is never silently
treated as legacy. This is a deterministic local consistency fingerprint, not
a cryptographic signature, model/provider identity attestation or proof of
case correctness. A writer who can replace both evidence and metadata can
forge it. Synthetic tests cover same-ID evidence changes, ordering, hidden
truth exclusion, legacy manifests and no-provider-call refusal. No private
truth, scoring formulas, F1/contamination thresholds or real customer data
were modified.

## Complete prediction-set binding in runner manifests

New completed-run metadata also records `selected_prediction_set_sha256`:
a domain-separated SHA-256 over the **complete persisted prediction rows** in
the selected case order, including assigned/unassigned IDs, workflow ordering,
uncertainty, and local blind-evidence digest fields. JSON object-key order is
normalized, so reformatting objects or changing JSONL row order alone does
not change the canonical selected-case prediction set. The fingerprint
does **not** include hidden answer keys or scoring ground truth.

On `--resume`, the runner verifies this field against the current selected
prediction rows **before** opening output artifacts or requesting a provider.
An edited assignment that still references only valid event IDs is rejected
when the completed manifest attests to different assignments. A present
malformed or mismatching digest fails closed; missing fields in older
manifests remain readable but set `resume_provenance_unverified: true`.
This uncertainty flag remains sticky on later resumes. The manifest
fingerprints the rows that existed when it was written; it does not attest
to interrupted runs with no completed manifest.

This is a local consistency check, not an authenticated signature or a
guarantee that the provider, source, or model is genuine: someone with write
access to both files can replace the prediction rows and recompute the
digest. It does not establish the 0.90 held-out assignment F1 or 0.05
contamination target, nor validate private holdout answers or real users.
Synthetic tests cover changed-but-valid assignments, exact replay, canonical
key ordering, legacy manifests, invalid digests and the no-provider-call
failure boundary.
