# Automation monitoring

Agent starters, n8n exports and the AI SDK target are deliberately
non-executing: you map tools, deploy and run the automation in your own
runtime. **Automation monitoring** (Evidence tab) closes the loop. Your
automation exports run reports and you import them; OWG shows how runs went
as the automation reported them, flags changes, and compares executed steps
with the reviewed design.

OWG never runs, pauses or changes an automation, and never verifies an
outcome. Everything shown is **reported by the automation**.

## Run report format

```json
{
  "format": "openworkgraph.automation-run-report.v1",
  "deployment": {"name": "Refund agent", "runtime": "ai-sdk",
                 "workflow_spec_sha256": "optional: digest of the starter's workflow.spec.json"},
  "runs": [{
    "run_id": "2024-03-01-0001",
    "started_at": "2024-03-01T09:00:00Z",
    "finished_at": "2024-03-01T09:05:00Z",
    "status": "succeeded",
    "steps": [
      {"step_id": "step_001", "activity": "Receive", "status": "completed", "at": "2024-03-01T09:00:00Z"},
      {"step_id": "step_002", "activity": "Validate", "status": "completed", "at": "2024-03-01T09:02:00Z"}
    ],
    "outcome": {"verified": true, "source": "crm"}
  }]
}
```

- `runtime`: `n8n`, `ai-sdk`, `openai-agents`, `claude-agent-sdk` or `other`.
- Run `status`: `succeeded`, `failed`, `handed_to_human`, `cancelled` or
  `running` (a running run has no `finished_at`). Step `status`: `completed`,
  `failed`, `skipped` or `handed_to_human`.
- `step_id` should be the starter's step IDs (`step_001`, …) so runs can be
  compared with the reviewed design.
- `outcome.verified` is whatever your automation decided (for example after
  reading the source system). OWG stores it as reported.
- Times need a timezone. Run IDs are unique per report, at most 128 printable
  ASCII characters; importing a run ID again replaces that run, so reports can
  be appended over time. A report holds up to 50,000 runs and 100 steps per
  run (64 MB).

`workflow_spec_sha256` is the starter's own digest: SHA-256 of UTF-8
`json.dumps(spec, sort_keys=True, allow_nan=False)`. If set, reports for a
different spec are refused once a design is attached.

### Producing reports

- **AI SDK starter**: append one run object per execution in your agent loop,
  using the step IDs from `workflow.spec.json`, and write the report file.
- **n8n**: add a Code node at the end of the workflow (and in the error
  workflow) that emits one run object built from `$execution` and the
  executed nodes; collect them with your usual export.
- **Agents SDK**: write a run object from your run result and tool-call trace.

## What you see

- **Reported results** for all runs, the recent window (default 7 days) and
  the baseline before it (default 28 days): succeeded, handed to a person and
  failed, as shares of finished runs with 95% Wilson intervals, plus the share
  of succeeded runs whose report says the outcome was verified.
- **Changes in the recent window**: an alert when the success rate drops, or
  the handoff or failure rate rises, and the Newcombe 95% interval of the
  difference excludes zero. Longer median duration is flagged in the same
  way, using a bootstrap interval. Without an alert, the intervals cannot
  distinguish a change.
- **Steps**: attempts, failure and handoff rates, and the median time since
  the previous step, sorted by problems.
- **Compared with the reviewed design** after you attach the starter's
  `workflow.spec.json`: runs that completed every design step in order, mean
  alignment fitness, design steps never completed, and step IDs that are not
  in the design.
- **Runs per week** by status.

**Analyse as event log** copies the reported steps into the event-log
workspace (case = run, activity = step, resource = step status), where
variants, KPIs, cohorts, simulation and conformance apply.

## Storage

Imports need **Store these runs on this computer**. Runs are kept in
`automation_runs.duckdb` (owner-only permissions) until you delete the
deployment, at most 50 deployments. All routes need the local dashboard
session; AI bearer tokens and MCP clients cannot read or change them.

| Route | Purpose |
| --- | --- |
| `GET /v1/automation-monitoring/deployments` | List deployments. |
| `POST /v1/automation-monitoring/reports?deployment_id=` | Import a report (JSON; `X-OWG-Local-Storage-Confirmed: true`). Without `deployment_id`, a new deployment is created. |
| `PUT /v1/automation-monitoring/deployments/{id}/design` | Attach the starter's `workflow.spec.json`. |
| `GET /v1/automation-monitoring/deployments/{id}/monitor?recent_days=7&baseline_days=28` | The monitoring report. |
| `POST /v1/automation-monitoring/deployments/{id}/event-log` | Copy the steps into the event-log workspace (`X-OWG-Local-Storage-Confirmed: true`). |
| `POST /v1/automation-monitoring/deployments/{id}/delete` | Delete the deployment and all its runs. |
