# Process dependencies and change impact

The shared `/processes` portal has a **Process dependencies and change impact**
workspace. An editor can declare that a consumer process uses the output or policy
of another process. Each declaration pins exact organization workflow IDs,
revisions and content digests, with an explicitly shared human explanation.

This connects the existing catalog without copying activity graphs or evidence.
Repository hierarchy maps remain independent organizational groupings. Dependency
links describe required prerequisites; both supported relationship kinds must form
a directed acyclic graph. They do not add executable subprocess calls, tool grants,
review inheritance, scheduling or benefit sums.

## Declare and inspect

1. Open a current shared process and choose **Load process dependencies**.
2. Load prerequisite choices, choose an existing process, and add it. Choices are
   paginated; the text filter searches only its loaded page.
3. Select **Uses output** or **Uses policy**, and explain the relationship. These
   are human assertions. The referenced source model's independent design review
   does not independently establish the relationship or policy authority.
4. Review the exact process pins and explanation, explicitly confirm organization
   sharing, and save. Every edit clears confirmation and saved-export eligibility.
   Saving an empty link list records that no prerequisites were declared; a missing
   declaration remains **unassessed**.
5. Load the current dependency graph. Choose a process, use the map's keyboard-
   accessible nodes, or inspect the full list. **Prerequisites** follow consumer →
   prerequisite arrows. **Potential consumers** follow the reverse direction and
   list processes whose saved declarations may need review after a change.

Distances are minimum edge counts. A diamond counts a reachable process once.
These are graph relationships from the complete bounded saved declaration scan,
not observed executions, discovered business causality or a claim that all actual
company dependencies are known. The prerequisite-first ordering is an inspection
order, not a business execution schedule. The map shows at most50 nodes and only
edges between them, with displayed/total counts; impact and full lists use the
entire accepted graph of up to200 processes.

No request loads customer source-system data or invokes an AI model. No observations,
private desktop evidence or independent process graphs are uploaded by this feature.

## Current basis and historical declarations

Each root retains up to20 immutable dependency revisions. History and separate
manifests identify the exact author, save time, dependencies and artifact digest.
Opening a historical dependency revision is read-only. The portal also makes
historical source-process selections read-only; open the latest design to edit.

The global view resolves each process's current latest model once, then compares
all saved pins with that revision and digest. This bounds resolution by unique
processes rather than repeatedly loading every historical pin. It shows:

- the current source state, including unavailable or corrupt models;
- whether every saved pin matches its latest source revision;
- that model's current independent design-review status;
- whether that process has a saved dependency declaration, including an explicit
  empty declaration;
- the dependency record revision, separately from source-model revision.

`current_declared_basis` requires every pin to match an available active latest,
independently reviewed source model, and every node to have a declaration.
Relationships and complete business coverage remain unverified even in this state.
Changed pins, review withdrawal, archives, deletion or unassessed nodes produce
`needs_reassessment`. Historical artifacts stay unchanged. Structural potential-
consumer lists remain available to help review changes against the saved graph;
they do not certify that historical relationships still apply to the changed model.

Use the explicit root/prerequisite refresh buttons, review the new design and
relationship, and save a new declaration. Source-model review and dependency
reassessment remain separate. A changed process never silently updates another
process's stored declarations or carries their review forward.

The view is a read snapshot with `checked_at`, not live monitoring. Known local
mutations/selections clear cached graph results. A global download performs a fresh
permission/snapshot/audit check and requires the previously displayed graph's exact
semantic fingerprint. Source/review/declaration changes require refresh before
export. The fingerprint includes organization, scanned records and current source
resolution; IDs and hashes identify artifacts, not anonymous or signed proof.

Normal JSON/BPMN/PDD/agent-project exports remain the existing source design.
Separate dependency manifests carry this lineage. Dependency changes invalidate
this feature's current basis; they do **not** automatically change normal model
review or ordinary implementation-export eligibility. Dependency-aware executable
build contracts and subprocess semantics remain R12/R13 work.

## Permissions, storage and concurrency

Current `org:process:read` is required for every read/resolution. Existing tenant-
wide process-read semantics apply; owner/editor/reviewer roles govern mutations,
not finer-grained process visibility. This feature adds no permission grants.

Saving requires `org:process:write`, current root owner/editor/admin role, active
root, matching access and declaration revision counters, explicit review/sharing,
and every referenced source model's active latest revision/digest. It does not
require edit permission on the referenced models. Reviewers without edit authority
cannot edit dependencies. Permission revocation and organization binding are
checked again inside the transaction, after the HTTP preflight.

Records reuse `workflow_knowledge` under the distinct three-component domain
`["organization-process-dependencies", organization_id, "v1"]`. No schema or
source-storage migration is introduced. Personal knowledge, maps, benefit plans,
opportunity assessments and process models retain their own domains.

PostgreSQL dependency saves acquire an organization-scoped transaction advisory
lock **before** locking involved process metadata in sorted workflow-ID order.
This serializes additions/replacements before the complete cycle check, so two
simultaneous individually valid edits cannot jointly create a cycle. SQLite uses
the existing `BEGIN IMMEDIATE` write transaction. Shared read snapshots use
PostgreSQL repeatable-read/read-only transactions or a SQLite read transaction.

Deleting edges cannot create a cycle. Erasure takes the current root metadata lock
without acquiring the graph-save advisory lock, avoiding an inverted lock order
when normal process deletion already holds that metadata lock. Source writes,
access changes and dependency writes serialize on the same metadata rows.

A root owner/admin with write scope can erase all its declaration history, even
when archived. Deleting a source root erases its own dependency history in the
same transaction as its source records. Other roots' explicitly shared declarations
and explanations remain, with their references visibly unavailable/unassessed.
No dependent source design or independent artifact is silently deleted. Audit
failures roll back mutations and withhold reads/exports. Audit payloads retain
only identifiers, revision/digest, counts and scan/focus diagnostics, without the
shared explanation or source/model text.

## Bounds and failure disclosure

Each declaration has at most20 distinct prerequisites and20 immutable revisions;
IDs and target process identities must be unique, with no self-dependency. Source
references use the existing strict workflow/revision/SHA256 contract. Explanation
text is nonblank, single-line valid Unicode, up to500 characters. Save bodies are
limited to64KiB; erase bodies to4KiB. Unknown/duplicate JSON keys and nonstandard
JSON values use the portal's existing strict parser and are refused.

The organization scan accepts at most100 root declarations and200 unique process
identities, with up to2,000 declared edges. A101st root, corrupt/misbound declaration,
invalid/cyclic/oversized graph or response above2MiB withholds the entire graph and
impact result with diagnostics. It never presents a successful partial impact
calculation. Limits apply to this declared dependency workspace, not the size of
the existing process catalog. Nothing is pruned to make an oversized scan pass.

Current source failure differs from an incomplete declaration scan: a valid saved
graph can still identify historical potential review candidates when a source has
changed or disappeared, with `current_basis:false` and visible diagnostics. This
never verifies current business dependency completeness or causal effects.

An owner can erase corrupt declaration history. Unexpected missing-root/orphan
records, storage tampering beyond supported counters or other database corruption
may require an administrator's backup/recovery procedure. Hashes detect mismatch;
they do not protect against an administrator who rewrites both content and hashes.

## Evidence and remaining work

The [UiPath process repository guide](https://docs.uipath.com/automation-hub/automation-cloud/latest/user-guide/about-the-process-repository)
was retrieved successfully on27 October2026. It describes linked solutions and
four total levels of subprocess organization. OWG's hierarchy maps cover that
organizational pattern; this separate dependency/change-impact contract advances
R12 in issue#370. No proprietary code, graphics or exact interface was copied.

`tests/test_process_dependencies.py` checks direction/distances/diamond reuse,
cycle prevention under concurrent opposing saves, same-root compare-and-swap,
independent-root edits, roles/scopes/revocation/tenant isolation, exact history,
source/review drift, no approval propagation, stale export refusal, archives and
root/history erasure, audit rollback, consistent snapshots during model commits,
corrupt declarations/model titles, bounds and strict HTTP preflight/parser paths.
Store tests run on SQLite and disposable PostgreSQL.

The real Chromium journey in `tests/browser/process_dependencies.cjs` covers
explicit sharing, both source/declaration revisions, the graph and keyboard impact
navigation, exact manifests, cycle refusal, dirty drafts/history, remote source
changes, explicit refresh/review, fresh exports, deletion, readers/tenants/held
old-identity responses, WCAG and mobile width. Fixtures do not certify customer
policy, actual business dependencies, enterprise scale or realized value.

Broader R12/R13 remain open: executable reusable subprocesses/call activities,
dependency-aware builds and review lifecycles, fine-grained visibility if introduced
elsewhere, richer relationship contracts, larger measured workspaces and full BPMN
interchange. Existing R15/R16 business-value and real-provider/company/reconstruction/
pilot acceptance gaps remain open. Full competitor parity is not established.
