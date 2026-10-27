# Shared opportunity to agent-project lineage

The company `/processes` portal can generate the existing SDK starter from an
exact current independently approved opportunity. A separate provenance manifest
connects opportunity, named approval snapshot, process revision, runtime
specification and archive hashes. OWG retains metadata only, not ZIPs, case inputs,
tool credentials, execution journals or source receipts.

This extends the shared company workspace. The local opportunity pipeline keeps
its own existing tracked-export behavior. Normal process JSON/BPMN/PDD/SDK exports,
model/specification hashes and all generated runtime entries remain unchanged;
the linked archive adds only `shared-opportunity.lineage.json`.

## Generate and inspect

1. Open the latest shared process and **Load agent project history**. The panel
   identifies the current saved opportunity revision and digest. Unsaved edits in
   the separate opportunity editor are excluded.
2. Review the exact saved source and confirm that you are generating a disabled
   starter only. **Generate linked project from current saved approved
   opportunity** requires current root owner/editor/admin rights with process
   write scope, current access revision, current registry revision and the exact
   current approved opportunity reference.
3. OWG checks the active latest independently reviewed process, current opportunity
   assessment/budget/source eligibility and exact named opportunity approval.
   Generation and metadata/audit persistence share the root metadata transaction
   lock; a concurrent source or role update cannot silently replace that basis.
4. The browser verifies the returned ZIP's SHA-256 and exact project-record hash,
   reloads its current source diagnostics, and downloads only when they match.
   HTTPS or localhost is required for the browser's cryptographic check. A
   tampered ZIP, changed identity, changed metadata or stale source withholds the
   download. A successful server generation may still have recorded metadata if
   the network/client download fails; inspect/reload and explicitly regenerate.
5. Select a saved artifact to inspect its exact pins, specification/archive
   digests, original generator actor/time and current source diagnostics.
   **Download selected saved project manifest** is an audited JSON view, with
   current diagnostics separate from the immutable saved record.

Only the existing SDK generation support is available: a confirmed model is not
automatically an implemented business integration. The starter still needs the
customer's reviewed tool mappings, runtime setup, authorized cases and current
execution intent. Its existing synthetic examples do not certify representative
customer behavior. No model/provider or business-system request occurs when OWG
generates or regenerates a ZIP.

## Exact artifact identity

The strict `openworkgraph.shared-opportunity-project.v1` manifest contains:

- organization identity and exact opportunity revision/digest;
- exact named **approved** opportunity snapshot, separately from a later
  implemented self-report snapshot;
- exact existing process revision/digest and assessment digest/assessed-in revision;
- the existing runtime specification digest and base-archive SHA-256;
- a deterministic project ID over the complete manifest basis;
- explicit false execution/spending/deployment/per-run-attribution/outcome flags.

The specification digest uses the existing runtime/monitor's canonicalization.
The source model, specification and every original archive entry remain byte
identical. A fixed-time ZIP entry holds the extra manifest. The registry then
stores the full linked-archive SHA-256, immutable original creator/time and record
digest. It contains no copied scenario, financial assessment values or model graph.
Hashes identify artifacts and references; they do not verify business policy or
execution results.

Repeated identical exports deduplicate to the same original record and ZIP, even
when another current editor requests the export. Every authorized generation
request remains audited. A changed opportunity snapshot, named approval, process,
specification or generator archive creates a different project identity. At most
fifty distinct records are retained per root; the bound refuses another distinct
export without pruning. Exact repeats remain possible at the bound.

## Current source basis and history

A record's `current_approved_assessment_basis` requires its original opportunity
snapshot to remain available with the exact digest, its exact process pin to
match the currently eligible independently reviewed design, its assessment digest
and assessed-in revision to remain current, and the same physical named approval
snapshot to remain the current approval basis.

A direct approved → implemented human stage report preserves the named approval
basis. The panel separately reports that the original opportunity snapshot is no
longer latest. Hold/reassessment/replacement approval, changed or withdrawn process
review, source change/archive/deletion/corruption invalidate present eligibility.
An identical replacement approval with the same actor/time/fields does not inherit
the old approval revision. No timestamps alone establish continuity.

Old manifests remain inspectable after source drift or opportunity erasure, with
explicit current blockers. Corrupt/incomplete/oversized project scans withhold the
whole artifact list and generation; an owner can erase supported corrupt history.
Owner erasure removes all registry metadata; root deletion removes it in the same
transaction. Opportunity erasure leaves minimal historical project metadata with
unavailable-source diagnostics. Downloaded copies remain outside server erasure.

Known same-page opportunity mutations immediately withhold cached project/source
eligibility and disable export/generation until reload. Other sessions' changes
require reload; mutation CAS and source checks remain authoritative. Selection,
disconnect and authentication rejection clear page data. Held old-identity JSON
or binary responses cannot restore a record or trigger a download. Server-side
records from an already completed authorized request may still exist.

## Regenerating an exact archive

OWG stores no archive. **Regenerate selected exact ZIP (hash check)** requires
current root edit/write rights, exact record/access references, current eligible
source basis and explicit confirmation. It uses the current installed generator
and the original process/manifest, then compares the base and full archive hashes.
An upgraded generator that cannot reproduce those exact bytes refuses the download
and preserves metadata. A new explicit generation can record a new artifact; OWG
does not silently substitute its bytes for an older record.

The panel labels archive reproduction **not rechecked** until that operation.
This is not a stored-binary repository, build reproduction guarantee, package
signature, deployment inventory or attestation of the binary that produced a run.
Keep downloaded implementation artifacts under the customer's own approved
version/build/deployment controls.

## Permissions, API and storage

Existing `org:process:read` is required for every operation. Reading/manifests are
currently tenant-wide under that scope, consistently with the shared process
catalog; this is not per-project confidential storage. Generate/regenerate also
require `org:process:write` and current owner/editor/admin role. A reviewer alone
can inspect metadata but cannot generate a tracked artifact. The current root
owner/admin with write can erase history, including after archive. Old source
process selections are read-only in the portal. Existing normal SDK read/export
permissions are not narrowed.

Endpoints under `/v1/human/processes/{workflow_id}/opportunity-projects`:

- `GET`: complete bounded metadata list plus separate current diagnostics.
- `POST`: expected opportunity pin, registry/access revisions and explicit review;
  returns ZIP plus project ID, full-archive digest and record-digest headers.
- `GET /{project_id}/manifest`: audited JSON view of one exact record.
- `POST /{project_id}/regenerate`: exact record digest, access revision and confirmation;
  returns only a hash-identical ZIP under current eligible source basis.
- `POST /delete`: current registry/access revisions and explicit all-history erasure.

Requests use the existing strict bounded/duplicate-key-rejecting parser and current
human authentication. Permissions are checked before parsing and again in the
locked write transaction. The separate JSON3 namespace
`['organization-opportunity-projects', organization_id, 'v1']` uses existing
`workflow_knowledge`; no schema migration, new default scope or service-token
fallback is introduced. Archives are bounded at4 MiB. Audit details retain IDs,
revisions and hashes, not archive bodies, labels or financial values. Failed
mutation audits roll back; failed read/export audits withhold the response.

## Execution and remaining lifecycle work

Opportunity approval and registry eligibility are export/lineage checks, not a
runtime kill switch or execution grant. The unchanged generated runner continues
to enforce its own exact current shared-design review, customer tool/source
permissions, explicit current intent, write preconditions, replay protection and
independent terminal-operation receipt checks. It does **not** read this registry
or continuously check opportunity stages. Reassessing an opportunity cannot
remotely cancel a separately authorized customer runtime; use its own execution
controls. Existing non-atomic-read/in-flight-action limits still apply.

This advances R5/R17 in #370/#264, following the public Mimica Maker pattern of
handing discovered process knowledge to implementation. No proprietary code,
graphics or interface was copied. Shared per-run/report/source-receipt association,
authenticated runtime adapters, deployment/version inventory, incident handling,
real-provider mappings, held-out customer cases and verified business value remain
work. A matching spec/hash or imported report never proves which binary executed,
an independently verified outcome or realized savings. Synthetic transport and
artifact checks do not close real-company/provider/reconstruction/pilot acceptance.
