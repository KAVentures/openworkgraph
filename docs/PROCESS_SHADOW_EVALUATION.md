# Review process-specific shadow cases

The downloaded [agent project](PROCESS_CATALOG.md#runnable-agent-project)
includes `evaluate.py`. It compares proposed routes and clarification requests
with explicitly reviewed customer expectations for the exact exported process.
It makes no business tool calls and never changes a saved process model.

Start with `shadow-cases.example.json`. Its authorization and review flags are
false. Select authorized case inputs, give each an opaque `case_1` style ID,
declare `normal`, `exception` or `missing_facts`, and review each expectation.
An expected `proposal` has one to eight accepted activity-ID routes; an expected
`needs_clarification` has no accepted routes. Route expectations cannot bypass
unconfirmed rules or manual review conditions in the process model. Review flags
are customer declarations, not organizational policy attestations.

The suite identifies `process_reference` (workflow ID, revision, content SHA256)
and `spec_sha256`. SHA256 values use UTF-8 encoded Python
`json.dumps(value, sort_keys=True, allow_nan=False)`, matching the exported
`execute.digest` helper. Editing a suite or specification changes its hash.
Each input is at most 64 KiB, the suite at most 256 KiB and 50 cases.

## Offline comparison

Capture planner outputs in this envelope:

```json
{
  "format": "openworkgraph.shadow-plan-results.v1",
  "process_reference": {"workflow_id": "SELECTED_ID", "revision": 1, "content_sha256": "EXACT_HASH"},
  "spec_sha256": "EXACT_SPEC_HASH",
  "suite_sha256": "EXACT_REVIEWED_SUITE_HASH",
  "results": [{
    "case_id": "case_1",
    "plan": {
      "revision": 1,
      "content_sha256": "EXACT_HASH",
      "status": "proposal",
      "activity_ids": ["EXACT_ENTRY_ID", "EXACT_TERMINAL_ID"],
      "missing_business_inputs": []
    }
  }]
}
```

Include every selected case exactly once. Copy the `plan` object from the
planner response; wrappers, stale references and duplicate case IDs are rejected.
The comparison needs no installed SDK, provider credentials or network:

```sh
python evaluate.py --cases reviewed-cases.json --results captured-plans.json --output shadow-report.json
```

Invalid individual plans count as mismatches. Malformed suite/result envelopes
stop comparison. Reports use opaque case IDs, input/artifact hashes, category,
matching counts and reason codes. Raw case values, model questions and process
text are excluded. Hashes identify content; they do not anonymize guessable data.
Reports are new files and never overwrite earlier reports. Exit status 2 means
mismatches require review; malformed input also returns a nonzero status.

## Explicit model evaluation

Install the generated requirements in an isolated virtual environment and
configure the chosen provider, then replace `--results` with `--model MODEL`.
This explicitly sends each selected case's input **values** and the process
model to that provider. Human expected routes and review fields are withheld.
The existing SDK planner has no business tools and disables SDK trace export.
Provider failures stop the run; there is no automatic retry or business fallback.

Agreement covers only the selected customer expectations. It does not establish
representative population coverage, rule truth, execution permission, source
outcomes, agent causality or financial savings. Inspect mismatches against
authorized evidence and current business rules before reviewing a new catalog
revision. An evaluation never approves, deploys or updates a process or agent.
