# Shared opportunity assessment and stage review

The company process portal supports one versioned shared opportunity record per
root process. It connects a five-question ease assessment, coordinator/team
labels, implementation-budget context and named stage decisions to an exact
shared process/scenario revision. It reuses current process ownership, membership
and scopes; creating an opportunity does not create another process or scenario.

[Implementation phase plans](PROCESS_IMPLEMENTATION_PLANS.md) add independently
reviewed phase baselines and separate human progress/cost reports over the exact
opportunity assessment. They do not change opportunity stage authority.

This complements the local automation pipeline and the separate benefit
contribution plans. Those features keep their existing behavior. Opportunity
approval does not change process-model approval, benefit inclusion, agent tool
permissions, spending authority or verified business outcomes.

## Use the portal

1. Open a process in `/processes` and **Load root opportunity**.
2. Enter a title, description and optional coordinator/team labels. Labels are
   descriptive; they grant no permissions. The current root process owner is
   shown separately and controls the opportunity's access through existing roles.
3. Save a shared idea, or **Add opportunity assessment**. Answer five questions
   from1 (low agreement) to5 (high agreement): clear rules, structured inputs,
   process stability, digital inputs and rare exceptions.
4. Enter implementation-budget currency and amount. The amount is a decimal
   string with at most two decimal places, capped at one billion. It is a stated
   budget context, not permission to purchase or incur costs. Optionally require
   a reviewer declaration that compliance review has been completed.
5. Review the exact draft and organization sharing before saving. With an
   assessment, the new record is **assessing**, with no retained approval. Without
   one it is an **idea**. Every edit, including labels or an equal-value
   reassessment, creates a new revision and clears opportunity approval.
6. A different current reviewer opens the saved snapshot, inspects the exact
   assessment/source/budget and records a named decision. The assessment author
   cannot approve their own assessment, including after changing roles.
7. An editor can explicitly report implementation only for the exact currently
   approved assessment. **Implemented remains a human self-report**. OWG does not
   build, deploy, execute, verify an outcome or certify savings from this action.

Draft edits disable decisions on the saved snapshot until saved/reloaded. The
manifest and source-navigation buttons identify the exact saved revisions; draft
changes are excluded from exports. Inventory entries are cleared after known
mutations, and loaded-page filters/pagination are explicit. Inventory status is
as of loading; refresh after changes.

## Ownership and decision permissions

All access requires the existing `org:process:read`. Reading is currently
tenant-wide under that explicit scope; collaborator removal revokes edit/review
rights, not process reading. No granular opportunity confidentiality is claimed.

| Action | Current root role and scope |
| --- | --- |
| Create/update assessment | owner/editor/admin plus `org:process:write` |
| Approve | owner/reviewer/admin plus `org:process:review`, different assessment author |
| Reject or place on hold | a current editor with write, or reviewer with review |
| Other stage moves / implementation self-report | current editor with write |
| Erase all history | owner/admin plus write, including after root archive |

Roles are checked again inside the locked mutation transaction. Expected
opportunity and root-access revisions protect simultaneous edits, approval versus
reassessment races, role revocation and ownership changes. Review decisions are
historical records, not current permission grants: a former reviewer remaining
named in history does not retain authority to make new decisions.

The existing process-only company SSO session also supports this workspace. It
still cannot read personal/work evidence or other portal sessions; logout and
scope revocation apply. No default scopes or service-token fallback are added.
Disconnect clears page memory, and held old-identity/old-selection responses
cannot restore protected state.

## Stages and exact approval basis

| Recorded stage | Available moves, subject to roles and eligibility |
| --- | --- |
| idea | assessing, rejected, on hold |
| assessing | approved, rejected, on hold |
| approved | implemented, on hold |
| on hold | assessing, rejected |
| rejected | idea |
| implemented | reassess through an explicitly saved new assessment |

An assessment digest covers the complete opportunity data, including exact process
reference, labels, questionnaire and budget context. The assessed-in revision and
actual author are recorded by the server. A stage decision must identify the
current revision, access revision and assessment digest. Approval additionally
records the independent decision actor, time and precise assessed-in revision.
Identical reassessment values do not inherit an old approval: the record has a new
revision, current approval is cleared, and old decisions remain in history only.

Approval and implementation reporting require all of:

- The pinned root process is available, active, integrity-checked, latest and
  independently design-reviewed. An opportunity decision does not substitute
  for that process review.
- A complete assessment and existing process `OpportunityScenario` are present.
  Impact/cost assumptions are read from that exact model; they are not copied or
  silently rebound to a newer scenario.
- Budget currency matches scenario currency. No currency conversion is performed.
- The scenario's one-time implementation-cost assumption does not exceed the
  entered implementation budget. Recurring operational costs are separate; this
  check does not certify actual spending or complete project finances.
- A required compliance-review declaration is explicitly acknowledged on approval.
  OWG records who declared it, but does not verify compliance evidence.

A process change, archive, corrupt payload, missing scenario or changed design
review can invalidate present eligibility without rewriting old history. A
latest record carrying an earlier **approved/implemented** decision then shows
effective state **needs_reassessment** and `current_approved_assessment: false`. It cannot progress
to implemented. Explicitly refresh the source pin, reassess and obtain a new
independent decision. An old approved badge alone is not a current-approval claim.

The source checks are point-in-time shared-design reads, not live business-system
verification. Historical design-review decisions can remain recorded after the
reviewer's permissions change, consistently with the existing process-review
contract. Current rights for each new request remain separately enforced.

## Scores and financial boundaries

Ease is `sum(answer - 1) × 5`, from0 to100. High ease means at least60. Modeled
impact uses the linked scenario's monthly cases, eligibility percentage and
minutes assumed saved per case; high impact means at least20 **unrounded** modeled
hours per month. Display rounding cannot move an input across that threshold.

| Ease / modeled impact | Quadrant |
| --- | --- |
| high / high | quick win |
| low / high | major project |
| high / low | fill in |
| low / low | deprioritize |

Hours and financial projections use the existing decimal scenario calculator.
These are user-supplied assumptions, not event-gap-derived labor measurements,
statistical confidence, realized savings or deployment readiness. Inventory
exposes no company savings sum; use separately reviewed benefit contributions
for nonduplicated modeled estimates. This opportunity approval neither includes a
scenario in those plans nor independently approves financial benefit claims.

## History, erasure and API

Up to50 immutable revisions per root retain assessment snapshots and decisions;
nothing is automatically pruned. Root owner/admin may erase all opportunity
history. Deleting the root process erases its opportunity history in the same
transaction, while normal models/evidence/benefit plans follow their own existing
deletion contracts. Downloaded manifests remain separate user-controlled copies.
A corrupt opportunity withholds its artifact/score; owner/admin can erase history.

Storage uses existing `workflow_knowledge` under
`["organization-process-opportunity-pipeline", organization, "v1"]`, distinct
from personal knowledge, shared models, repository maps and benefit plans. No
schema migration is added. Mutation and minimal audit commit atomically; failures
roll back both. Reads use consistent snapshots, with a separate required audit;
audit failure withholds the response. Audit fields are IDs/revisions/digests and
stage, not labels, notes, questionnaire answers, budgets or scenario values.

Human process authentication and no-store responses apply to:

- `GET /v1/human/process-opportunity-pipeline?after=…&limit=…`: keyset inventory,
  default50/max100, filters in the UI cover only the loaded page.
- `GET/POST /v1/human/processes/{id}/opportunity`: inspect or explicitly shared save.
- `GET …/opportunity?revision=N`: exact historical snapshot plus current diagnostics.
- `POST …/opportunity/transition`: explicit current revision/access/assessment-basis
  review and named stage decision.
- `GET …/opportunity/export?revision=N`: freshly authenticated/audited manifest.
- `POST …/opportunity/delete`: owner/admin-confirmed complete-history erasure.

Save bodies are bounded to64 KiB; decisions/erasure to4 KiB. Duplicate JSON keys,
nonstandard numbers, unknown fields, unbounded text, forged approval/state/author
fields and wrong source references are refused. The server creates authors,
assessment basis, approval and decision stamps. Normal process/BPMN/PDD/SDK export
schemas and hashes remain unchanged; these records are separate lineage.

## Research and remaining scope

The public [UiPath idea cost-benefit guide](https://docs.uipath.com/automation-hub/automation-cloud/latest/user-guide/performing-the-idea-cost-benefit-analysis)
was retrieved successfully on26 October2026. It distinguishes a pre-implementation
baseline from subsequent actual/new-estimate phase costs and durations. OWG's
current budget check is only stated cap context over the existing modeled cost;
it does not implement those actual phase ledgers or certify realized value. The
shared stage/assessment flow extends OWG's existing local Automation Hub-style
pipeline using its current roles and exact model evidence; no proprietary code,
branding or assets are copied.

R15/R16 remain partial: multiple opportunities per process, dedicated ownership
independent of root roles, dependencies/phase budgets/schedules, collaboration,
independent benefit approval and allocation, source-backed implementation/outcome
receipts, measured baseline/intervention/adoption/operational costs and verified
realized value remain in #370. Synthetic fixtures do not replace real-company,
provider, reconstruction-quality or consenting multi-team pilot acceptance.


Historical opportunity views and exports set `viewing_historical_revision:true`
and effective state `historical_snapshot`, with current approval false and no
allowed transitions. This applies after reassessment, hold and implementation,
even when the source design or assessment digest is unchanged. The stored artifact
retains its exact recorded stage/approval. Open the latest opportunity revision
to inspect current eligibility. Source-resolution/review blockers remain visible;
no historical approval is presented as approval of the latest assessment.
