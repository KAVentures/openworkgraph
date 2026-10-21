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

## Inspect a report in the local catalog

Open the exact saved design and use **Choose shadow evaluation report**, then
**Inspect report against saved revision**. Selecting a file sends nothing. The
explicit inspection sends the minimized JSON only to the authenticated local
OWG server, which validates its schema, case/reason totals and exact exported
specification hash. Extra raw fields, contradictory counts, authority claims
and other specifications are rejected with fixed diagnostics.

The result shows matching case counts and per-case mismatch reasons beside the
saved process. It warns when that immutable revision has a newer saved version.
Unsaved editor changes are excluded. Editing the draft, changing the selected
file, clearing the review, switching revisions or reloading clears the display;
late responses cannot restore a review invalidated by an edit.

This verifies artifact association and internal report consistency only. A
customer could fabricate a self-consistent report; authenticity, expectation
truth, population coverage and source outcomes remain unverified. The report's
timestamp is a customer claim. Inspection creates no knowledge revision, retains
no report, copies no case values and invokes no model, source adapter or business
tool. Downloaded reports remain independent files under customer control.

`POST /v1/process-catalog/shadow-review` accepts `workflow_id`, strict integer
`revision` and the minimized `report`, within the existing 128 KiB bounded JSON
limit. It requires a local dashboard session; ordinary API/MCP bearers cannot
upload these reports. Responses use `Cache-Control: no-store`.

## Inspect an organization project's report

The Gateway's **Shared processes** page supports **Minimized shared shadow
report** for an opened exact revision. Its separate inspection button explicitly
sends the report to that organization's Gateway. Browser validation first rejects
extra fields, case/model text, native case IDs, malformed metadata and other
process references. Selection alone sends nothing. The server repeats strict
validation, binds the specification to the authenticated organization, and
reports whether the revision is still latest, active and independently reviewed.
Neither a design review nor matching evaluation expectations grants execution.

Use a report from that organization's SDK project: local projects and other
organizations produce different specification hashes even when their process
IDs and graphs match. Reading historical reports is permitted without approving
their implementation. Changing the selected revision, candidate, file or identity
clears the temporary display; late responses cannot restore it.

The JSON report is not retained. A minimal organization audit records actor,
process/revision, case count and matching count, without case IDs, case hashes,
suite/results hashes or model text. Hashes in an uploaded report are identifiers,
not anonymization. Existing process-read scope is required, tenant isolation is
preserved and private personal knowledge is not searched.

`POST /v1/human/processes/{workflow_id}/shadow-review` uses the same selected
`workflow_id`, strict integer `revision` and minimized `report` body as local
inspection. The Gateway's bounded JSON, process-only session/delegated identity
and no-store response controls apply. Inspection changes no model or review,
calls no business/source/model service and claims no independent business truth.
