# Paired task-context agent evaluation

This is a task-context-specific extension of the existing product-value and
matched-agent evaluation methodology. It does not replace the continuity
scorer or claim model quality from retrieval-byte benchmarks.

## Preregister before collecting results

Select development and independently held-out real, authorized workflows.
Freeze the same task and source-system snapshot for all arms, and retain
the exact serialized requests and model settings outside this report.
Use a sandbox or read-only replay; never allow real payments, messages or
other consequential operations in an evaluation.

- control: same task and source access without OWG context.
- observed: same task/source access and ordinary observed task context.
- augmented: same observed context plus explicitly selected reviewed knowledge
  and known-record companion when applicable.

Keep the model, settings, source access, instructions (apart from the
intended context ablation), output budget and evaluation rubric matched.
Use fresh sessions; rotate order using the frozen slots. Rotation is
deterministic, not randomized; choose independent cases and preregister
their order before observing results. Record every failure and timeout.
Retain collector-side transport logs, exact request and response bytes,
tool traces and context fingerprints privately. Do not equate retrieval
with model consumption or use.

## Offline API

`evals.task_context_ablation.plan(cases, settings, replicates=2)` returns
the exact immutable run-slot manifest and plan SHA-256. Cases contain
case_id, fold (development or holdout), source_state_sha256, task_sha256
and request_sha256_by_arm (control, observed, augmented).
Settings contains model_id and settings_sha256. Hash exact frozen bytes
with SHA-256; keep the independent plan pin before running. Two to five
replicates per case are supported.

`score(frozen, expected_pin, runs)` validates submitted attempt receipts.
Each receipt contains slot_id, model_id, settings_sha256, request_sha256,
source_state_sha256, status (completed/failed/timeout), output_sha256
and review. A completed attempt requires a reviewer ID and review
bound to the same output digest, task_success and
consequential_wrong_action booleans, and nonnegative wrong_assumptions.
For failed/timeout attempts, output_sha256 and review are null. Missing
slots remain missing. Use blinded output review and independently verified
ground truth outside this helper; the helper cannot authenticate reviewers
or transport, check exact request content or prove the arms differ only
in context. A successful response with a consequential wrong action is
not counted as reviewed success.

Reports preserve planned denominators, incomplete attempts, and separate
development/holdout counts. No causal inference, superiority or rollout
decision is made. Do not use synthetic scorer fixtures as agent evidence.

## Combined candidate validation

Sibling PRs #1708 and #1709 share the same parent #1707. Their changed
file inventories are disjoint. The integrated candidate retains their
exact changed blobs. Local helper suite: 80 passing tests, including
the combined retrieval and companion helpers and ten new manifest/scoring
tests. Full application, transport, persisted database and real external
model runs remain unverified. Run the existing integrated CI before merge.
