# Issue #226: matched A/B/C agent evaluation

Run the offline evaluator after collecting **real** agent executions:

    python scripts/evaluate_record_graph_issue226.py --cases /private/cases.json --runs /private/runs.json --minimum-replicates 5 --output /private/report.json

**No model evaluation was performed by adding this file.** It cannot invent
executions, source access, or held-out examples. Source-log and OWG invocation
receipts must come from the tool orchestrator/runner, NOT an agent's self-report.

Each case has an opaque case_id, independently adjudicated gold outcome,
development/holdout fold, optional exact opaque OWG reference, and stressors.
Required stressors include interleaved work, absent references, source outages,
stale states, divergent clocks, inconsistent IDs and cross-device data.

Each actual execution provides case_id, condition (A/B/C), replicate,
predicted_outcome and host_tool_logs.

- A: baseline tools only.
- B: baseline tools with source-system audit access.
- C: identical B access plus OWG MCP, isolating incremental OWG value.
- Every arm sees the same case and replicate seed. Missing matched runs
  invalidate the report rather than biasing the success rate.
- C is counted as using OWG only when the runner captured a successful
  get_record_graph on the matching exact opaque reference.
- B and C must attempt an equivalent matched source-log read; successful
  source reads require the same runner-observed SHA-256 source snapshot,
  while equal explicit outage statuses remain comparable. Unknown states
  are not silently dropped.
- Cases without a reference cannot be required to call OWG.
- Report accuracy differences and Wilson uncertainty, without inventing an
  improvement claim.

**Privacy:** use opaque IDs and normalized outcomes only. Keep held-out
real cases and runner logs outside the public repository.

**Limitations:** this script evaluates supplied run records, does not
orchestrate live agents or cryptographically attest to tool calls. It cannot
establish that OWG beats system logs. An independently supervised live
benchmark with real authorized data is still required to close issue #226.
