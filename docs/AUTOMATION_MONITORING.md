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
`json.dumps(spec, sort_keys=True, allow_nan=False)`. The first supplied digest
pins the deployment, including when reports arrive before the design. Later
reports declaring a different digest and mismatched design attachments are
refused. Use a separate deployment for another specification version. The
declared pin is returned as `reported_spec_sha256`; it is automation-supplied
metadata, not independent verification of the model or its review.

Reports without a digest remain supported and do not clear an existing pin.
Older stored reports did not retain their declared digest, so migration leaves
that value unknown rather than reconstructing it from an attached design.

### Producing reports

- **AI SDK starter**: append one run object per execution in your agent loop,
  using the step IDs from `workflow.spec.json`, and write the report file.
- **n8n**: add a Code node at the end of the workflow (and in the error
  workflow) that emits one run object built from `$execution` and the
  executed nodes; collect them with your usual export.
- **Generated OpenAI Agents SDK project**: after a separately authorized
  customer execution, run `python monitor.py --journal
  execution-journal/INTENT_DIGEST.jsonl --deployment-name "My implementation"
  --output new-run-report.json` in that project. Import the resulting file here.
  The offline command makes no provider, tool or OWG calls and never overwrites
  an existing output. The journal must include the exact specification digest;
  older journals without it cannot be retroactively attributed to a spec.
  Only actual tool attempt IDs, timestamps, statuses and the opaque intent digest
  are exported. Case data, arguments, results, credentials and source references
  are excluded. Planned human steps and routing nodes are not invented.
  An unfinished journal remains running; uncertain effects require reconciliation.
  A reported success means the runner's declared route checks matched, not that
  the business objective was achieved. `outcome.verified` remains unknown, even
  if the journal reports successful source checks. Attach that project's exact
  `workflow.spec.json` to compare reported activity IDs against the saved graph.
- **Other Agents SDK implementations**: write a run object from your run result
  and tool-call trace.

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
- **SDK graph comparison** accepts a versioned SDK project's exact spec as a
  separate design format. Its immutable normalized graph and declared revision
  reference are retained locally with the deployment; no catalog is modified or
  queried and current independent review is not established by attachment.
  Completed activity IDs are aligned against the full graph, including paired
  forks/joins, alternative branches and loops. Repeated labels cannot combine
  distinct activities. Routing nodes are silent; unreported human activities
  remain missing evidence rather than being invented or treated as proven
  process deviations. Failed or uncertain steps never count as completed.
  The view shows exact structural matches, mean minimum activity edits **among
  compared runs**, missing reported activities and unexpected step IDs. A match
  does not verify business rules, policy, human work or outcomes. At most 128
  distinct completed paths are selected by frequency with deterministic ties;
  parallel search also uses the existing 200,000-state batch budget. Coverage
  and the counts excluded for each limit or for having no completed steps stay
  visible. Unknown runs are not counted as matches or assigned a fitness value.
- **Runs per week** by status.

**Analyse as event log** copies the reported steps into the event-log
workspace (case = run, activity = step, resource = step status), where
variants, KPIs, cohorts, simulation and conformance apply.

## Storage

Imports need **Store these runs on this computer**. Runs are kept in
`automation_runs.duckdb` (owner-only permissions) until you delete the
deployment, including attached design snapshots, at most 50 deployments. All routes need the local dashboard
session; AI bearer tokens and MCP clients cannot read or change them.

| Route | Purpose |
| --- | --- |
| `GET /v1/automation-monitoring/deployments` | List deployments. |
| `POST /v1/automation-monitoring/reports?deployment_id=` | Import a report (JSON; `X-OWG-Local-Storage-Confirmed: true`). Without `deployment_id`, a new deployment is created. |
| `PUT /v1/automation-monitoring/deployments/{id}/design` | Attach the starter's `workflow.spec.json`. |
| `GET /v1/automation-monitoring/deployments/{id}/monitor?recent_days=7&baseline_days=28` | The monitoring report. |
| `POST /v1/automation-monitoring/deployments/{id}/event-log` | Copy the steps into the event-log workspace (`X-OWG-Local-Storage-Confirmed: true`). |
| `POST /v1/automation-monitoring/deployments/{id}/delete` | Delete the deployment and all its runs. |

## Signed report channels

Instead of exporting a file and importing it, an automation can push its run
reports to OWG itself. On a deployment, choose **Signed report channel**, then
**Create report channel**. The channel secret is shown once. Store it in your
automation's secret store; OWG never shows it again, and **Rotate secret**
replaces it.

The automation POSTs to `/v1/automation-monitoring/channels/{channel_id}/reports`
with:

- the local API token (`Authorization: Bearer …`), like every `/v1` request;
- `Content-Type: application/json` and this body:
  `{"format": "openworkgraph.signed-run-report.v1", "version_label": "v1.4.0", "report": { …run report… }}`;
- `X-OWG-Report-Timestamp`: Unix seconds, within five minutes of OWG's clock;
- `X-OWG-Report-Sequence`: 1, 2, 3 … for this channel;
- `X-OWG-Report-Signature`: `v1=` followed by the hex HMAC-SHA256, keyed with the
  secret, of `"{timestamp}.{sequence}."` plus the exact body bytes.

`shared/automation_report_signing.py` implements the signature with the standard
library only, so it can be copied into a Python runtime. Any HMAC-SHA256
implementation works: an n8n Crypto node, Node's `crypto.createHmac`, or Web Crypto.

What OWG does with each request:

| Request | Result |
|---|---|
| Wrong signature, missing headers or a stale timestamp | `401`; nothing stored; a *rejected signatures* incident |
| Exact resend of a sequence number (e.g. after a lost response) | `200` with `duplicate: true`; nothing stored again |
| A used sequence number with different bytes | `409`; a *reused sequence numbers* incident |
| A sequence number more than 2,000 below the highest seen | `409` |
| A higher sequence number that skips some | Imported; the skipped numbers are listed as missing and counted in a *lost reports* incident until they arrive |
| A report for a different workflow spec than the deployment's | `409`; nothing stored or rebound; a *version drift* incident naming the new spec hash and version label |
| A revoked channel | `403` |

Each accepted report adds to the channel's version inventory. For each
`(version_label, workflow_spec_sha256)` it records first and last seen, reports
and runs. A deployment has at most one live channel, and deleting the deployment
deletes its channels and incidents.

## Incidents

Imported runs, whether signed or imported by hand, and channel problems are
grouped into deduplicated incidents:

- *Failed runs* and *Runs handed to a person*, keyed by their first failing or
  handed-off step;
- *Rejected signatures*, *Reused sequence numbers*, *Lost reports* and *Version drift*.

Each incident keeps occurrence counts, first and last seen, up to ten sample run
IDs and your notes. **Show incidents** lists them with **Acknowledge**,
**Resolve** and **Reopen** buttons.

- A triage decision names the incident's last-seen time, so a decision made
  before new occurrences arrived is refused.
- A new occurrence after resolution reopens the incident and counts the reopening.
- Triage records your decision only. It never re-runs an automation, replays a
  business write or contacts a provider; every response says so.

Dashboard-session routes:

| Route | Purpose |
|---|---|
| `POST /v1/automation-monitoring/deployments/{id}/channel` | Create a channel |
| `GET /v1/automation-monitoring/channels` | List channels |
| `POST …/channels/{id}/rotate` | Rotate the secret |
| `POST …/channels/{id}/revoke` | Revoke the channel |
| `GET /v1/automation-monitoring/incidents?status=&deployment_id=` | List incidents |
| `POST …/incidents/{id}/triage` | Triage, with `status`, `note` and optional `expected_last_seen` |
