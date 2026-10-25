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

## Tracked agent project exports

An approved or implemented idea with an exact current local process link can
**Download tracked agent project**. The server checks the idea revision and
linked process before generating, and checks the process again afterward. A
stale edit, changed/deleted/replaced process, failed source check, unapproved
idea or descriptive-only reference refuses a new export.

The download uses the existing SDK generator. Its specification, executable
files, disabled mappings and separate customer-intent/permission requirements
are unchanged. The additional `opportunity.lineage.json` names the idea and its
revision, exact process reference, specification digest, generator archive
digest and project ID. It grants no execution or design-review authority.

Only references, digests and the first export time are retained in the existing
local pipeline database, at most 50 distinct artifacts per idea. Identical
exports reuse the same record; a generator change produces a different artifact
identity even if the specification is unchanged. The archive itself is not
stored. Deleting the idea deletes these records; downloaded copies remain
outside OWG. A failed generation records nothing. Historical artifact metadata
remains after reassessment or source deletion, with current-revision/source
status reported separately. Exporting never changes the idea's revision/state.

This proves which bytes were generated for a selected local decision. It does
not prove deployment, customer execution, testing, causal benefit or business
outcomes. Checks are point-in-time, not a transaction across both source stores.
The project-to-run-to-independent-receipt connection remains open in #370 M0/R5.

Dashboard-session-only APIs:
`POST /v1/automation-pipeline/{id}/agent-project` with `expected_revision` returns
the ZIP and `X-OWG-Project-ID` / `X-OWG-Archive-SHA256` headers;
`GET /v1/automation-pipeline/{id}/projects` lists bounded metadata. Both are
no-store. The server serializes local idea edits while exporting and rechecks
source identity; a later source change cannot revoke copies already downloaded.

## Project to imported-report links

**Review imported run reports** explicitly links a recorded project to a
selected local monitoring deployment. Both the attached design's hash and the
retained report specification hash must match the project's exact specification.
An unhashed report, unattached design, different specification or unavailable
monitoring store refuses the association. Matching names, times or activity
sequences are never substituted for these hashes.

The pipeline stores only project/deployment identifiers and the first link time,
at most ten links per project. Repeating a link preserves that time. Reading
links rechecks current monitoring metadata in one bounded query; deletion or
mismatch becomes unavailable/mismatched, never a silent replacement. An explicit
remove action deletes only the link. Deleting an idea deletes its projects and
links, while monitoring reports and downloaded archives remain separate.
Historical projects can be associated with their historical reports after idea
or process changes; this records historical evidence and grants no current
approval or execution authority. Optimistic idea revision checks still apply.

These are **reported specification associations**. A retained hash claim does
not establish per-run attribution: monitoring deployments may also contain
unhashed legacy runs. Neither successful runs nor reported `verified` outcomes
become independently verified execution, business outcomes, deployment or savings.
Those claims remain false in every link response. Independent source receipts
and real customer acceptance remain open in #370.

Dashboard-session-only, no-store routes:
`GET /v1/automation-pipeline/{id}/project-reports`,
`POST` to the same path to link, and `POST …/project-reports/unlink` to remove.
Mutations require `project_id`, `deployment_id` and `expected_revision`.

## Explicit source-state check downloads

**Check selected source state** selects a recorded project, one source-state
expectation confirmed in its exact saved process, and an already-observed
normalized `owg:r:` reference. A separate checkbox confirms this exact source
read and receipt download; changing the record/expectation/project clears it.
No source read occurs when opening the controls or listing export metadata.
Existing provider opt-ins, authorized source access, retained observation,
identity completeness and receipt freshness remain enforced by the existing
`source_operation_receipt` adapter. Confirmation does not enable any source
setting or recording. Unknown/refused source data remains unknown.

The JSON download binds that source check to the selected idea/project/process
and specification hash. Its receipt verifies at most a provider-defined
operational state at the indicated time. The relationship to a reported run or
business case is user-selected, and is not verified. Agent causality, artifact
execution, business objectives, customer outcomes and execution authorization
are not established. Historical saved goals can be checked; source state may
change immediately afterward. The existing catalog check-outcome API is unchanged.

Only the export ID, project ID, idea revision, time and document SHA-256 are kept
in the existing pipeline database (at most 50 records per project). Receipt
bodies, observed-record references, selected operations and verified-state values
are not stored. Export history never rereads a provider. Deleting the idea clears
this metadata; downloaded files are separate user-controlled copies. An exact
same document reuses metadata. The cap is checked before any source read.

Database locks are released across the external read. The idea revision, project
and pinned model are checked again before an export record is committed. A stale
or deleted context discards the new export; it cannot undo an already authorized
in-flight read. The checksum records bytes generated for a requested check, not
successful file delivery, current provider truth or proof of execution.

All APIs require the local dashboard session and use no-store responses:
`GET …/{idea}/projects/{project}/source-check-options` and `source-check-exports`,
and `POST …/source-check-export` with `expected_revision`, `terminal_activity_id`,
`expected_operation`, `resource_ref` and `source_read_and_export_confirmed:true`.
The POST returns the JSON document with `X-OWG-Source-Export-ID` and
`X-OWG-Document-SHA256`. These digests are local provenance, not public signatures.
