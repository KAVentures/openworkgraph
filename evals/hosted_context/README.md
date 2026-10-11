# Real CLI execution

`run_clients.py` is an evaluation controller, not the evaluated assistant. It
launches a new CLI process and empty working directory for every case/condition/
trial, with the **unchanged case prompt** as the sole user request. It never
adds the expected tools, target IDs, grading rubric, or this conversation.

The enabled condition exposes the production hosted MCP catalog and its normal
initialization instructions through a transparent stdio proxy. The disabled
condition has no OWG server. Both use medium reasoning effort, provider-default
sampling, no shell/filesystem/browser/source-system tools, no local desktop
skills, no subagents, no resume, and no controller instructions. This is a
hosted-tool-only condition, not a test of every possible installed skill bundle.
The two onboarding case prompts already mention OWG/ChatGPT in the original
corpus; they must not be described as entirely implicit prompts.

The Gateway is created from scratch on loopback using SQLite. Synthetic test
accounts enroll using single-use grants; ingestion uses the normal authenticated
API and restrictive connector policy. Only the synthetic populated identity
opts into structural agent sharing, which is turned off after ingestion. The
empty and local-only accounts have zero synced events. MCP uses genuine signed
RS256 bearer tokens and the production verifier, authorization, tool handlers,
redaction and pagination. A separate loopback JWKS listener avoids deadlocking
the verifier's synchronous JWKS fetch. No ambient OWG URL, database, OAuth
issuer, or production credential is used. Loopback HTTP is a test-only transport
limitation; the production resource identifier remains HTTPS.

The proxy logs actual MCP requests/responses independently of model prose. Only
safe argument summaries, event-shaped returned IDs, counts and error flags enter
results. Interrupted requests remain visible without invented responses. Raw
responses are forwarded in memory and never saved. CLI streams, disposable
credentials and databases are temporary and deleted after collection. Completed
answers contain only synthetic-context model output and can be reviewed.

## Reproduce

Use an authenticated Codex CLI and/or Claude Code CLI. Configure provider
credentials securely outside the repository; never put them in output files or
CLI arguments. Authentication is inherited, not copied or printed by the runner.

```bash
python -m venv .venv
.venv/bin/pip install -r evals/hosted_context/results/dependencies.lock.txt
.venv/bin/pip install --no-deps -e .
.venv/bin/python -m evals.hosted_context.run_clients \
  --out /tmp/owg-new-results \
  --codex /path/to/codex --claude /path/to/claude \
  --codex-model gpt-5.4 --claude-model claude-sonnet-4-6 \
  --workers 4 --trials 1 --timeout 60
.venv/bin/pytest -q tests/test_real_hosted_context_clients.py \
  tests/test_hosted_context_routing_score.py
```

Use a new output directory. Exact CLI versions and requested model identifiers
are saved. Provider model aliases must not be relabeled as verified immutable
backend snapshots. Record observed snapshots if the provider exposes them.

`attempts.jsonl` contains all launched attempts, including blocked sessions.
`traces.jsonl` contains **only successfully completed model turns**. A completed
no-call session can have `harness_recorded=true`; a failed authentication attempt
cannot. `routing.json` uses the existing scorer and is empty when no model
completed. `catalogs.json`, `seed.json`, and `manifest.json` capture configuration.
Costs are reported CLI costs, not inferred from token counts or subscriptions.

## Interpretation and independent review

Never count authentication failure as missed invocation, successful negative
specificity, or unsupported prose. Never count a controller transport test as a
model trial. Check enabled catalog availability before accepting a completed
positive run. Compare conditions within each client; native client prompts
prevent a perfectly controlled cross-client causal comparison.

Keep routing metrics separate from task quality. For completed answers, a reviewer
who has not seen the routing grade should record each factual claim, its returned
evidence, support/unsupported/unknown, and any inferred authorization. Score
task completion separately: 0=no useful continuity, 1=partial grounded continuity,
2=answers the requested continuity question with correct evidence and uncertainty.
Insufficient evidence can be a correct answer, especially onboarding, current
status and misleading adjacency. The synthetic corpus cannot establish completion
of live business actions without authorized source-system connectors.

Compare paired enabled/disabled task scores, not invocation rates alone. Report
positive first-tool accuracy separately from no-call negatives, and required-tool
recall on cases with nonempty `must_call` separately from vacuous successes.
Chronological fallback requires query-free trace calls; event IDs alone do not
prove exhaustive pagination or factual entailment. Audit returned pagination
before claiming exhaustive history. With repetitions, report Wilson intervals
for proportions and paired task-score changes with uncertainty. With zero
completed turns, every behavior metric and interval is **unmeasured**.
