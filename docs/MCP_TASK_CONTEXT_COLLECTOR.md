# Collect matched task-context model runs

Use the existing task-context ablation plan to pre-register authorized,
read-only tasks and exact requests. This collector adds a local CLI for
one planned attempt at a time; it does not collect source evidence, contact
business systems, run agent tools or perform independent review.

## Prepare a trial

Use a fresh private directory (chmod 700 on POSIX). Prepare each exact UTF-8
JSON request with keys model, messages, temperature, max_tokens and stream
(false). Messages must be exactly one system and one user message, plain text
only. No tools, credentials, streaming or arbitrary endpoint URLs are accepted.
Keep model and settings fixed and source state frozen for each matched case.
Hash the exact request file bytes with SHA-256, and set settings_sha256 to
the existing ablation digest of the request object excluding messages.
Freeze the plan with task_context_ablation.plan and independently retain the
plan pin. The collector cannot prove the arm requests differ only in context.
Use authorized, nonproduction replay materials.

Run slots in the frozen order, one process at a time:

```sh
python -m evals.task_context_collector run --plan plan.json \
  --plan-sha256 PLAN_PIN --slot SLOT_ID --request request.json \
  --directory private-runs --port 11434 --timeout 120 --confirm-loopback
```

The collector sends exactly the pinned bytes once to the OpenAI-compatible
/v1/chat/completions endpoint on literal 127.0.0.1. The listener must be
operator-controlled; a loopback service may proxy elsewhere. Inspect and
authorize the material accordingly. No inference-location, model identity
or independent transport attestation is made. No retries, redirects or
tool calls. The timeout is a socket timeout, not a hard wall-clock deadline.

Each slot reserves its own exclusive directory before sending. Private
request and started files are followed by the exact bounded successful-HTTP
JSON response body, exact assistant output bytes if normally completed, and
a receipt. Non-200/oversize responses are not retained; malformed bounded
HTTP-200 JSON bodies are retained for audit. Failures and timeouts are recorded
without graded outputs. Interrupted directories remain missing attempts,
and cannot be reused. Continue the next slot only after the previous invocation
has stopped. Do not copy real evidence or response files into the repository.
The local directory is not an immutable or tamper-proof ledger.

## Independent review and collection

Blind outputs to arm assignment outside this tool and have an independent
reviewer assess the original task and independently established rubric.
Create a JSON object mapping slot IDs to review objects with reviewer_id,
output_sha256, task_success, consequential_wrong_action and wrong_assumptions
as defined by the ablation scorer. Bind reviews to the exact output text
bytes. No review is permitted for an uncompleted attempt.

```sh
python -m evals.task_context_collector collect --plan plan.json \
  --plan-sha256 PLAN_PIN --directory private-runs --reviews reviews.json \
  --output collected-report.json
```

The collector recomputes request/settings pins, response/output fingerprints
and exact output extraction for completed attempts, checks review binding and
invokes the existing scorer. Missing and interrupted attempts remain in the
planned denominator. A completed output must have an independent review
before scoring. No causal inference or superiority claim is made.

Validation: 85 focused tests passed locally (80 previous plus five new).
The new tests use actual loopback HTTP with simulated endpoint responses
and cover exact bytes, failure retention, refused tool output, replay refusal,
review binding, tampering and interrupted slots. No real model was available
in the development environment. Full CI and actual independently adjudicated
model evaluation remain required.
