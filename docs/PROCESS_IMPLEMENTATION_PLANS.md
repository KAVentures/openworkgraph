# Implementation phase baselines and human reports

The company `/processes` portal supports a separate implementation plan over an
existing root opportunity. Plan phase costs, effort and calendar dates, obtain a
named independent baseline review, then record human-reported amounts and
remaining estimates without changing the reviewed baseline. There is one plan
history per root, not a second process, opportunity or scenario store.

## Use the portal

1. Save an opportunity assessment with budget currency and amount. Open its root
   process, **Load implementation plan**, then **Start phase baseline from
   opportunity**. Enter a title and up to twelve phases, each with a stable ID,
   name, cost, effort hours, planned start and finish dates.
2. Review the exact estimates and confirm organization sharing before saving.
   A draft can precede opportunity approval and can exceed its budget context;
   it cannot be frozen until the eligibility checks below pass.
3. A different current reviewer inspects the saved baseline and approved
   opportunity context, then **Independently freeze saved baseline**. The named
   review binds the exact baseline digest, assessed-in revision and exact
   approved opportunity revision/digest. This is planning review only.
4. An editor separately enters a phase's reported cost/effort to date, remaining
   cost/effort estimate, status and optional actual dates. Confirm sharing before
   saving changed reports. Each changed phase records its actual server-side
   submitter, time and revision; unchanged phases retain their attribution.
5. To change the plan, edit the baseline or refresh its opportunity reference and
   save a new baseline revision. Every save, including equal values, clears its
   review and records the new baseline author. Obtain a fresh independent review.
   Reports and their attribution are retained. A phase with a saved report cannot
   be removed or renamed; explicitly report cancellation rather than repurposing
   its identity. Prior baseline versions remain in history.

Editing reports disables baseline edits and vice versa. Edits reset sharing
confirmations; dirty drafts cannot be frozen or downloaded as saved data. Use
**Load implementation plan** to discard draft edits and inspect current state.
Totals are labeled as saved data and include a check timestamp. A known
same-page opportunity change immediately withholds the old current-review and
budget claims, disables mutations/exports, and preserves unsaved visible inputs
until reload. Other sessions' changes require reload; write CAS and source
checks enforce current state independently of the page snapshot.

## Amounts, dates and uncertainty

All amounts are nonnegative decimal **strings**, at most two decimal places,
normalized to two places. Each cost is capped at one billion; each effort amount
at one million hours. Currency is USD, EUR, GBP or SEK and must match the pinned
opportunity budget. There is no currency conversion. Zero must be entered
explicitly: an empty amount is unknown, never zero.

| Saved quantity | Calculation |
| --- | --- |
| Baseline cost / effort | Sum of the declared phase estimates |
| Calendar span | Earliest planned start to latest planned finish, inclusive |
| Cost / effort to date | Sum only when every baseline phase has a known corresponding amount |
| Remaining cost / effort | Sum only when every baseline phase has a known corresponding estimate |
| New total estimate | Actual to date plus remaining estimate, only when both totals are known |
| Variance from baseline | New total estimate minus baseline, only when known |

Each reported quantity shows known/unknown phase coverage. A partial total is
withheld rather than presented as the complete implementation cost. Calculations
use decimal arithmetic. Overlapping phases are allowed; calendar span is not
summed phase duration, working days, utilization, a critical path or evidence of
effort. No automatic scheduling, working calendar, launch date or phase
dependencies are inferred.

Statuses are planned, in progress, on hold, completed and cancelled. Completed or
cancelled reports require explicit self-report confirmation, a finish date,
known cost/effort to date and explicitly zero remaining cost/effort. Completed
also requires a start date. Ordered Gregorian dates are validated. Completion
and cancellation remain human declarations; they do not independently verify
execution, spending, business outcomes or adoption. Reports can explicitly
replace earlier status/amounts; their immutable history remains available.

Reported actuals and forecasts exceeding budget context are retained with
separate flags. Recording a historical cost does not authorize that cost or new
spending. Reports remain writable when an opportunity becomes stale/unavailable,
provided the root is active and current edit permission remains: reporting old
activity must not silently renew a stale baseline review.

## Exact review basis and permissions

Freezing requires a valid latest active independently reviewed process, current
independently approved opportunity assessment, compatible scenario/budget
currency and baseline cost within the opportunity's implementation budget. The
assessment's exact digest and assessed-in revision must still match the baseline
pin. The reviewer must differ from the actual baseline author. A current-source
change, reassessment, hold or replacement approval invalidates present baseline
eligibility without rewriting history. A direct approved-to-implemented human
stage report preserves the same named approval basis. A later replacement
approval requires a new baseline assessment/review even if its fields/time match
an older approval. Freeze requests CAS the exact approved opportunity revision.

| Action | Existing scope and current root role |
| --- | --- |
| Read/export | `org:process:read`, currently tenant-wide |
| Baseline/report update | read + `org:process:write`, owner/editor/admin |
| Freeze baseline | read + `org:process:review`, owner/reviewer/admin, independent author |
| Erase all history | read + write, owner/admin, including after archive |

Permissions, root access revision and plan revision are checked again under the
existing root metadata transaction lock. Simultaneous reports cannot silently
overwrite each other. A reviewer named in history retains no authority for new
requests after permission removal. Archived roots block updates/review. Reading
is tenant-wide under explicit process-read scope; this is not confidential
per-project financial storage. Enter only information authorized for that scope.
No new default scopes, personal evidence access, source-system privileges or
service-token fallback are added.

## History, storage and API

Each baseline save, review or report update creates an immutable snapshot, with
at most fifty revisions per root. At the bound, writes fail without pruning.
Old snapshots and downloads show `historical_snapshot` and
`current_reviewed_baseline: false`; the saved artifact remains exact. Current
source diagnostics are separate from historical saved declarations. Invalid
artifacts withhold amounts and export. Owners can erase even corrupt history.

Endpoints under `/v1/human/processes/{workflow_id}/implementation-plan`:

- `GET` latest, or `?revision=N`; `GET /export?revision=N` for an audited manifest.
- `POST` baseline with expected plan/access revisions and sharing confirmation.
- `POST /reports` with exact baseline digest, expected revisions and changed
  phase reports; each submitted phase replaces that whole report, not other phases.
- `POST /freeze` with exact baseline digest, current approved-opportunity pin,
  expected revisions and explicit independent review confirmation.
- `POST /delete` with expected revisions and explicit all-history erasure.

The strict `openworkgraph.implementation-phase-plan.v1` contract uses a separate
JSON3 `['organization-process-implementation-plans', organization_id, 'v1']`
namespace in existing `workflow_knowledge`; there is no database migration.
Audit details contain IDs/revisions/digests, not cost values, titles or phase
names. Failed mutation audits roll back; failed read/export audits withhold the
response. Owner erasure removes this history; root deletion erases it in the
same transaction. Opportunity erasure leaves plan history as explicitly stale
human data. Disconnect/selection changes clear page memory, including financial
DOM state, and ignore held old-identity responses.

Plans are separate from normal model, BPMN, PDD and agent-project exports, process
dependency graphs, benefit contribution totals and source receipts. Baseline
review never grants spending or execution authority. Actual values and realized
savings remain unverified. No receipt, budget or stage declaration establishes
business value by itself.

## Competitor reference and remaining work

The public [UiPath Automation Hub cost-benefit guide](https://docs.uipath.com/automation-hub/automation-cloud/latest/user-guide/performing-the-idea-cost-benefit-analysis)
was checked on 2026-10-27. It describes per-phase baseline dates, actual/new
estimates, implementation people costs, working-day sequencing, launch dates,
robot capacity assumptions and detailed running costs. OWG now covers separately
reviewed calendar/cost/effort baselines and human-reported forecasts. Working-day
scheduling, critical paths/dependencies, dedicated multi-opportunity ownership,
launch/adoption lifecycle, detailed operating costs, independently approved
benefit allocations and verified realized value remain work under #370/#264.
This is an original functional implementation; no proprietary code or assets
were copied. Synthetic tests do not establish real-company or financial accuracy
acceptance.
