# Versioned process repository maps

The Gateway **Shared processes** portal now organizes existing designs into
explicit, versioned repository hierarchy maps. Select a shared root design and
choose **Load root repository map**, load process choices, add children under
selected parent nodes, or move/remove branches. Each occurrence pins an exact
organization process ID, revision and content digest. The map shares its title,
category, capability and references only after explicit review/sharing confirmation.
Process choices are paginated; their text filter applies only to the loaded page.

The separate **Process repository maps** inventory navigates saved maps and filters
its loaded page by title/category/capability. It displays up to 100 maps per page
with an explicit next-page control. Categories and capabilities are descriptive
organization taxonomy labels, not role grants, staff assignments or model rules.

Maps use the existing append-only workflow-knowledge table under a dedicated
organization namespace. They do not copy process graphs or evidence. Each root
can retain up to 20 immutable map revisions, with at most 100 occurrences and four
total levels **including the root**. Node IDs are unique; unknown parents, cycles,
a process repeated in its own ancestry, and duplicate sibling processes are
refused. A process may be reused under different branches. Branch order is a
saved layout choice, not observed chronology or an executable sequence.

Each map is a complete, independent repository view. A child occurrence does not
automatically expand another map rooted at that process. Separate maps can show
alternative decompositions; their links are not a global executable dependency
graph. Full reusable subprocess/call-activity semantics remain separate work.

## Versions and live diagnostics

Load an exact saved map revision from **Saved map history**. Historical map
artifacts remain immutable while current diagnostics are recomputed separately:

- **current**: the exact referenced revision still exists and is latest/active;
- **revision_changed**: the pinned version still exists, but a newer one is saved;
- **archived**: the referenced process is currently archived;
- **unavailable**: the process or exact revision no longer exists;
- **integrity_failed**: its stored payload/hash no longer matches the reference.

Available titles come from the pinned historical record, rather than a later
renamed process. Unavailable/corrupt references expose no source title or model
fields. The original reference remains in the immutable map. Refresh each chosen
reference explicitly and review/save a new map; refresh does not edit that design
or update other references silently. Saves require the latest root model pin,
active available child revisions, current root access counter and map revision.
An explicitly retained older child revision may be saved; it is still marked
changed. Roots that changed must be refreshed before saving.

Model review is reported for each exact process revision and is separate from
whether it remains active/latest. Confirming a grouping never approves the child
models or grants execution authority. The map reports declared occurrence and
unique-process counts only; it does not sum case effort, cost or savings. Benefit
rollups, overlap allocation and realized value still need their own evidence.

## Editing, privacy and lifecycle

Every operation requires current `org:process:read` in the authenticated tenant.
Existing shared-process reading is tenant-wide; removing a collaborator removes
editing/review rights, not design reading. Analysis publication and raw-case grants
remain distinct and are not used here.

Map writes also require `org:process:write` and current root owner/editor/admin
permission. Referencing a child requires read access; it does not require or grant
editing that child. Saves lock distinct referenced process metadata rows in stable
ID order, verify pins and use map/access CAS. Concurrent maps sharing processes
cannot bypass revision checks. SQLite and PostgreSQL use the existing transaction
seams; write audits commit atomically. Audits retain IDs/revisions/digests/counts,
not names, categories, capability values, full maps, model bodies or evidence.
Read/export audit failure withholds the response.

Only a current root owner or process administrator with write scope can erase all
map history. Erasure is allowed even for archived or corrupt maps and preserves
process designs. Deleting the root process erases all its map revisions atomically.
Deleting a child preserves other maps' declared references and marks them
unavailable; its model and label are not copied into those maps. Metadata in map
titles/category/capability is an independent reviewed disclosure and must be
removed separately if necessary.

The portal clears map drafts, choices, history and inventory on identity changes,
and suppresses held responses from old selections/identities. Failed current
permission checks clear protected map data. Historical maps are read-only; unsaved
edits disable export of a mismatching saved map. Credentials/map data are not
persisted by the new client module. Explicit manifest exports are freshly
human-authorized and audited. Once downloaded, a file follows the recipient's
retention/deletion policy; future revocation cannot erase that copy.

## Interchange and execution boundary

**Download saved map manifest** exports the immutable map artifact and separately
computed current reference diagnostics. The artifact's digest stays reproducible
across model changes; live diagnostics may differ. A malformed artifact is refused
for export, but its authorized maintenance revision remains available for erasure.

The existing process-model JSON/BPMN/PDD and agent-project exports retain their
existing schemas and hashes. They cover **only the selected flat activity graph**.
Repository hierarchy is not included in those implementation specifications and
is explicitly identified as excluded in the map UI and manifest. Use the separate
map manifest to preserve grouping references. No hierarchy is flattened into
invented executable steps, no parent review propagates to children, and no
implementation generator claims to execute the repository decomposition.

This implements a bounded R12 repository navigation/versioning slice from issue 370.
Global process dependencies, capability-map analytics, BPMN subprocess execution,
shared benefit governance and real-company/pilot acceptance remain open.

## API

Base: `/v1/human/processes/{root_workflow_id}/hierarchy`.
All successful responses are `no-store`; existing process-session or delegated
human auth applies. No service, evidence or analysis credentials are accepted.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/v1/human/process-hierarchies?after=…&limit=…` | Keyset page of latest map metadata, limit 1–100 |
| GET | base, optional `?revision=1…20` | Saved artifact, current diagnostics, permissions/CAS |
| POST | base | Confirm/share typed hierarchy, expected map/access revisions; 64 KiB body |
| GET | base + `/export?revision=N` | Audited exact saved map plus live diagnostics |
| POST | base + `/delete` | Owner/admin erasure of all map history, CAS and confirmation; 4 KiB body |

Map definitions use `openworkgraph.process-repository-hierarchy.v1`; saved artifacts
use `openworkgraph.saved-process-hierarchy.v1`. The organization ID is explicit at
map level, and every node reference resolves only in that organization namespace.
Duplicate JSON keys, extra fields, nonstandard numbers, invalid Unicode text and executable
flags are refused. Digests check OWG record consistency, not independent business
source truth or approved policy.

## Upgrade and storage separation

This release changes the **internal** shared-process knowledge key from the legacy
untagged two-component owner to a three-component versioned domain. Personal
knowledge uses two components. Repository maps also use a separate three-component
domain. Special organization/actor names cannot make personal knowledge address
shared process/map records.

Before upgrading an existing Gateway, take a database backup and **stop all old
Gateway worker versions**, then start the upgraded service. Do not run old and new
versions concurrently during this upgrade. Downgrades require restoring the
compatible backup. Process IDs, revisions, payload bytes, hashes, collaborators,
reviews, model/specification exports and external organization references stay
unchanged. Storage keys are internal and are not exported process references.

Startup migrates only existing revisions attributed in the shared-process author
ledger. Unattributed/personal rows remain in their existing private domain. The
migration and its counts-only per-organization audit/marker commit together;
interruption rolls back and retry is idempotent. A conflicting destination payload or a root without attributable shared history
fails closed instead of replacing either record; restore/reconcile the backup
before serving requests. The marker avoids rescanning history on normal restarts.
The migration does not independently certify historical business evidence or
repair already inconsistent legacy records.

Review status now counts only decisions whose recorded content digest matches
the exact current record for that revision. Mismatched decisions remain in the
review ledger but cannot approve a different payload or enable implementation
export. Normal existing reviews retain their status without reapproval.


For a separate consumer-to-prerequisite contract and global declared change-impact view, see [Process dependencies](PROCESS_DEPENDENCIES.md). Hierarchy groupings remain independent views and do not supply dependency edges.
