# Independent blinded review of task-context trials

After collecting attempts with task_context_collector, use the new
task_context_blinded_review CLI to prepare reviewer material. Select
authorized nonproduction tasks, independently establish a rubric and
inspect every exact output for sensitive material and condition cues.
Do not publish raw business information. Blinding is not guaranteed:
outputs may mention their evidence or condition. Suspected unblinding
must be documented separately.

Provide materials.json, an object keyed by every planned case ID.
Each value contains task (exact UTF-8 text matching the preregistered
task_sha256), rubric, ground_truth_reviewer_id, and approved: true.
Keep the rubric fixed before observing results. The tool verifies the
task hash; it cannot establish when the rubric was written.

```sh
python -m evals.task_context_blinded_review prepare --plan plan.json \
  --plan-sha256 PLAN_PIN --directory private-runs --materials materials.json \
  --review-dir reviewer-packet --private-dir private-key --confirm-reviewed-material
```

Retain the printed key digest independently. Send only the approved
packet and review template to the independent reviewer, never the key,
run inventory or condition assignment. The packet uses random opaque
IDs and shuffled order, and preserves exact output text rather than
silently rewriting it. New attempts invalidate a frozen packet.
Completed outputs all require review; failed, timed-out, missing and
interrupted slots stay in the planned denominator. Use a smaller
preregistered study if the 8 MiB packet bound is exceeded.

The reviewer fills reviewer_id, task_success, consequential_wrong_action
and wrong_assumptions for each opaque review ID. Blank review fields
cannot be scored. The declared ground-truth reviewer cannot act as the
output reviewer. Actual independence and blinding are not authenticated.

```sh
python -m evals.task_context_blinded_review score --plan plan.json \
  --plan-sha256 PLAN_PIN --directory private-runs --materials materials.json \
  --packet reviewer-packet/packet.json --key private-key/key.json \
  --key-sha256 KEY_PIN --reviews completed-reviews.json --output report.json
```

This rechecks the exact packet, key, approved material, verified attempt
inventory and output bindings before using the existing collector and
ablation scorer. No new model requests, deployment or causal claim.
Do not change the run directory during preparation or scoring; the
separate reads do not constitute an atomic filesystem snapshot.

Validation: 89 focused tests passed locally, including four new tests
with simulated loopback HTTP completions. No actual model trial or
independent human review was performed. Full combined CI remains required.
