# Automation pipeline

Roadmap #326 F4: a local counterpart to UiPath Automation Hub. It is a reviewable list of
candidate automations with explicit states, an ease questionnaire, an impact estimate based
only on your own assumptions, and a recorded history. It appears as the "Automation
pipeline" card on the Evidence tab.

## States

`idea → assessing → approved → implemented`, with `rejected` and `on_hold` along the way:

| From | Allowed moves |
|---|---|
| idea | assessing, rejected, on hold |
| assessing | approved (only after an assessment), rejected, on hold |
| on hold | assessing, rejected |
| approved | implemented, on hold |
| rejected | idea (reopen) |
| implemented | — |

Every move is kept in the history with its time and optional note (the last 100 entries).
Every change must send `expected_revision`. A stale edit is refused with HTTP 409 instead of
overwriting someone else's change.

Saving an assessment on an **approved** idea returns it to **assessing**. A fresh
approval must review the new assumptions before the idea can become implemented.
The state change, previous and new assessment, and revision are saved atomically
in the bounded history. New approvals also retain the assessment they reviewed;
expand **Assessment at this decision** to inspect it. This remains a local review
decision and never grants execution permission. Legacy history entries remain
readable; historical assessment snapshots that were never stored are unknown.

## Assessment and priority

- **Ease (0–100):** five questions answered from 1 to 5 (decisions follow clear rules, inputs
  are structured, the process rarely changes, inputs arrive digitally, exceptions are rare).
- **Modeled hours per month:** cases per month × minutes per case × eligible share ÷ 60, using
  only the numbers you enter.
- **Quadrant:** quick win (ease ≥ 60 and ≥ 20 h/month), major project (high impact, lower
  ease), fill-in (high ease, lower impact) or deprioritize. The thresholds are returned with
  every result.
- A compliance-review flag can be recorded.

For costs, payback and comparing implementation options, use the opportunity scenario
calculator ([PROCESS_OPPORTUNITY_REVIEW.md](PROCESS_OPPORTUNITY_REVIEW.md)).

## Boundaries

- "Approved" records a decision in this local list only. `execution_authorized` and
  `approval_authorizes_execution` are always false. Nothing is run, built, sent or published,
  and no process model changes.
- Modeled hours are not measured savings (`savings_claimed: false`).
- Data stays on this computer in `automation_pipeline.sqlite3` (owner-only permissions), with
  at most 1,000 ideas. Deleting an idea removes it.
- The CSV export prefixes cells that start with `=`, `+`, `-` or `@`, so spreadsheets do not
  run them as formulas.

## API (dashboard session only)

`GET /v1/automation-pipeline[?state=]`, `POST /v1/automation-pipeline`,
`GET /v1/automation-pipeline/{id}`, `POST …/{id}/assess`, `POST …/{id}/transition`,
`POST …/{id}/delete`, `GET /v1/automation-pipeline/export.csv`.

## Exact saved-process links

Open a saved local process and choose **Add saved revision to automation pipeline**.
The server resolves that immutable revision and checks its content hash before
creating an idea. It records only the local workflow ID, revision and content
hash alongside the idea; source observations, case identifiers and process rules
are not copied. Unsaved editor changes are excluded. Manually entered source
references remain descriptive strings and cannot assert a resolved link.

The idea shows whether the pinned process still exists and is current, and can
reopen the exact linked revision. Refreshing status is read-only. Approval and
moving to implemented recheck linked revisions on the server; a changed, deleted,
replaced or uncheckable process refuses the decision. To use a different revision,
open it and create a new idea. Existing ideas and assessment history are not
silently rebound or rewritten. Approval history retains the selected reference.

Checks are point-in-time local catalog reads, not an atomic transaction spanning
both stores or continuous monitoring. Source changes can occur after a check;
later decisions check again. They do not verify independent review, business
outcomes, policy, tool grants or deployment readiness. Legacy ideas keep their
existing behavior. This is the process-to-opportunity part of #370 M0/R5, not yet
full log/cohort → project → execution → independently verified receipt lineage.

Dashboard-session-only APIs: `POST /v1/automation-pipeline/from-process` with
`workflow_id`, `revision` and `content_sha256`, and
`GET /v1/automation-pipeline/{id}/process-status`. Both are no-store.
