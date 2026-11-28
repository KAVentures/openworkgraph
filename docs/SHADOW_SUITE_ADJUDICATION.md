# Reconcile reviewed shadow case expectations (R17/R25)

Two customer reviewers can independently prepare reviewed-shadow-cases.v1
suites using the same exact process specification, case IDs, categories and
case inputs. This local utility identifies differences in expected planner
status and complete accepted route sets before a customer freezes an
evaluation cohort. It is separate from the existing product-pilot outcome
adjudication and model-result scoring.

From the repository, compare the two private reviewer suites using their
independently retained canonical JSON SHA-256 pins:

```sh
python -m shared.shadow_suite_adjudication compare \
  --reviewer-a reviewer-a.json --a-sha256 EXACT_A_PIN \
  --reviewer-b reviewer-b.json --b-sha256 EXACT_B_PIN \
  --output disagreement.json --confirm-local-review
```

The report enumerates all cases and distinguishes status disagreement
(proposal versus clarification), accepted-route-set disagreement and exact
agreement. The order of alternative accepted routes is irrelevant, but the
activity order within each route is preserved. Duplicate routes are refused.
Case inputs and categories must match exactly, and neither case may be
dropped. The report includes opaque IDs and counts but does not copy case
values or route contents. Review the private original suites to adjudicate.

An authorized human adjudicator chooses reviewer a or b for every disputed
case, with no extras. For example, save a decisions file:

```json
{"decisions":{"case_1":"a","case_7":"b"}}
```

Run the following with the same inputs and the review pin printed by compare:

```sh
python -m shared.shadow_suite_adjudication reconcile \
  --reviewer-a reviewer-a.json --a-sha256 EXACT_A_PIN \
  --reviewer-b reviewer-b.json --b-sha256 EXACT_B_PIN \
  --review-sha256 EXACT_REVIEW_PIN --decisions decisions.json \
  --output new-adjudicated-suite --confirm-local-review
```

There is no automatic union, intersection or majority decision on disputed
route sets. If neither reviewer is correct, revise the independent reviewed
inputs and repeat comparison. An empty decisions object is valid only when
all expectations already agree. The new directory contains
reviewed-cases.json and receipt.json, with exact input-review, decision and
output pins. The suite itself contains the original private case inputs.
Both modes refuse existing output locations. Discard incomplete outputs
if a write fails. The files are created with restrictive permissions on
platforms supporting POSIX permissions.

This standalone tool accepts the base runtime suite shape, validates input
shape and consistency but does not replay routes against the process model.
Validate the final suite with the exported runtime's existing validate_suite
before evaluation or freeze. Agreement and adjudication do not establish
ground truth, actual independence, reviewer identity, signatures, blinding,
case representativeness or business outcomes. No model or provider is called
and no execution authorization is granted.

The isolated test suite checks all 729 pairs of three-case reviewer
expectations, route-order normalization, duplicate-route refusal, stale
pins, case changes, incomplete adjudication and exclusive file creation.

```sh
python -m pytest -q tests/test_shadow_suite_adjudication.py
```
