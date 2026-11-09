# R9: manual local numeric cohort aggregation

This is an **opt-in, local operator command**, not an authenticated saved-log API or dashboard feature. It accepts a JSON cohort that the operator has already been authorized to read, never connects to a provider or OWG saved-log database, and writes only aggregate numbers to stdout.

Create a local file named `cohort.json`:

```json
{"definition":{"version":1,"expression":{"field":"duration"}},"numeric_fields":["duration"],"rows":[{"duration":2},{"duration":4}]}
```

Run `python -m server.event_log_projection_cli cohort.json`. The output includes rows, non-null/null counts, sum, mean, minimum, maximum and definition digest. Source identifiers, source rows and private columns are not printed. A nonzero exit code and generic stderr indicate refusal. Files larger than 256 KiB, symlinks, duplicate numeric field names, unknown top-level fields, malformed values and projection budgets are refused.

**Security contract:** the input must already be authorized, consented, retention-valid and appropriately scoped by its human/operator source. This command does not establish tenant isolation, ownership or current revocation state, and must **never** be exposed as a remotely callable API or used as a substitute for saved-log permission checks. Local files remain the operator's responsibility. Results are descriptive, not verified workflows, causes or worker performance.

Run focused checks with `python -m pytest -q tests/test_event_log_projection_cli.py tests/test_event_log_metric_projection.py`. The authenticated saved-log API and dashboard integration, end-to-end browser acceptance and real-provider validation are separate follow-up work.
