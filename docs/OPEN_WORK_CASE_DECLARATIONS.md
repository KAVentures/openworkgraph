# Local Open Work case declarations (bounded foundation)

`server/open_work_case_declarations.py` adds a **disabled-by-default, internal-only** durable owner-declaration ledger for issue #247 Milestone 3. It complements the existing `open_work_reviews` human-reviewed queue, `open_work_model.compose_case_graph` source/observation projection, `record_graph` and `open_work_outcomes.source_operation_receipt`. It does **not** replace them or register a new REST/MCP tool, browser surface, background scan, scheduler or agent runtime.

## Stored facts and provenance

A trusted local caller can explicitly `save_case_declaration` only for an opaque `owg:w:` item already in the local reviewed queue. The caller must supply an **account-bound opaque `owg:scope:` identifier** from its own verified authorization context; this module cannot authenticate a provider or derive the binding itself. It accepts a fixed, non-textual owner-declared `goal_code` (`review_source_state`, `assess_declared_policy`, `verify_operation`), an `expected_outcome_code` (`human_review_recorded`, `source_state_rechecked`, `operation_receipt_reviewed`), and at most eight explicitly declared dependency references to other reviewed cases in the **same scope**. Self-edges, duplicate edges and cycles are rejected. It does not infer obligations or dependencies from clicks, temporal proximity, agent telemetry or a source status.

The stored row includes a per-incarnation monotonic `revision`, a random opaque 128-bit `edit_token`, creation/update/expiry timestamps, opaque refs and codes only. **At save time, expiry is capped by the admitting human review's own expiry and every declared prerequisite's current expiry.** A declaration created later than its review cannot survive the review's original deadline; a dependent declaration cannot outlive an earlier-expiring prerequisite and reappear if that prerequisite is reviewed again. CAS updates may shorten an expiry when a new prerequisite is declared or retention is reduced, but never renew it—even if the dependency is later removed. A lapsed review requires a fresh explicit declaration, not just a renewed review. **Replacing a human review before its expiry also invalidates the prior owner's declaration**: reads require the declaration's original creation to be at least as recent as the admitting review's `created_at`. That invalidation closes transitively over declared dependencies, and the next write prunes those stale approvals before allowing a fresh declaration. Review creation/expiry windows are rechecked under the declaration write transaction; a changed review window between admission snapshots refuses the save. Review timestamps are not cryptographic generation identifiers: a replacement recorded at the exact same timestamp cannot be distinguished in this v1 schema. The trusted local caller must not expose this as a multi-process tenant authorization guarantee. Write-time expiry/retention pruning also **transitively erases** declarations that depend on an expired or unreviewed prerequisite. It additionally erases dependent chains when a prerequisite row is **missing from the auxiliary ledger or belongs to a different opaque scope**: merely hiding an orphan on reads is insufficient, because re-declaring the missing prerequisite could otherwise resurrect old dependent approvals. This scope-aware closure is checked before inserting any newly declared case, and does not reinterpret a deleted edge as an independent owner-approved obligation. This prevents a later re-declaration of that prerequisite from reviving a dependent whose prior expiry was longer (for example, after a prerequisite's CAS edit shortened its lifetime). The read-time fixed-point filter still suppresses all such dependents before cleanup occurs. It never contains customer text, titles, native provider identifiers, credentials, amounts, source status, provider receipts, workflow observations or free-text instructions. A new declaration requires `expected_revision=0` and no token; every update requires **both** the current exact `revision` and the `expected_edit_token` returned with that same incarnation. Concurrent writers are serialized by SQLite `BEGIN IMMEDIATE`; stale edits are rejected, not silently merged. A successful save returns the exact row read **inside its write transaction**, before `COMMIT` releases the lock. If another local writer deletes or revises that row immediately after the commit, the first caller receives its own committed revision and incarnation token, not the competing writer's data or a post-commit missing-row assertion. This is a point-in-time save receipt, **not** a guarantee that the row is still current when the caller receives it; callers must re-read for current authority. **Any successful CAS revision of a prerequisite transactionally erases its transitive dependent declarations**, even if the prerequisite's codes are unchanged: v1 dependencies refer to an opaque case, not a pinned prerequisite revision, so carrying a dependent owner's earlier approval to a different revision would be unsafe. Failed CAS/cycle checks roll back without erasing dependents; unrelated cases remain. Affected owners must explicitly declare fresh dependencies (revision 0), while their independent review requests remain. This is conservative invalidation, not inferred approval, provider verification or a new permission. Deleting a reviewed case deletes its declaration **and all transitively dependent declarations**, rather than silently stripping owner-declared prerequisite edges and presenting previously blocked work as independent. Unrelated declarations remain intact. Owners must explicitly re-declare any replacement graph; a stale revision cannot resurrect a deleted dependent declaration. If the auxiliary ledger is corrupt or unreadable, review deletion instead erases the entire auxiliary ledger rather than leaving a revoked case behind. A missing review suppresses the declaration from reads even if an interrupted cleanup left a row. A dependency is presented only if its own declaration is current and reviewed, **and all of its transitive prerequisites are also current and reviewed**; if A expires while B depends on A and C depends on B, neither B nor C is returned. Unrelated current declarations remain visible. This is a read-time safety filter, not an inferred business-status change or automatic source recheck.

Every read says `source_state=unknown_until_explicit_recheck` and `source_read_performed=false`. Owner-declared expected outcomes are **not** verified outcomes; business obligation, customer outcome and agent execution authorization remain false. An independent fresh provider receipt from `open_work_outcomes` must still be obtained through the existing authorized reader; the ledger neither stores nor grants that authority. No source status or agent execution is cached as truth.

## Lifecycle and security

The local SQLite file lives in the existing private `auth_dir` with mode `0600` on POSIX. It is created **only** by an explicit save of an already reviewed item. Symlinks, non-regular files, unsafe permissions and unsupported schema versions fail closed. Storage is bounded to 50 declarations and a maximum seven-day TTL, further restricted by the user's human-history retention setting; `ephemeral` retention forbids saves and erases the file on read. Existing review removal erases the corresponding declaration and its transitive dependent declarations, and source replacement/disconnect and session-only history-policy cleanup erase the ledger through the existing `clear_review_intentions` lifecycle. Reads never perform provider/network calls or create a database.

This is a **single-installation local foundation**, not a production org/actor-scoped durable graph or a tenant-wide access-control service. The opaque scope must be supplied by an independently trusted account-bound caller; there is intentionally no untrusted API entry point yet. A declaration save checks current review admission and history retention both before opening storage and again inside its SQLite write transaction. If a source disconnect, review deletion or session-only retention switch occurred between those checks, the save fails closed; deterministic tests exercise all three cases. This narrows a stale-snapshot admission race, but does not establish a cross-process atomic fence. Cross-process source-revocation racing a concurrent save is not a distributed transaction: every read still requires a current reviewed item, but full provider-grant epoch fencing and atomic multi-store erasure remain necessary before exposing writes in a company deployment. Source outage, stale/partial/conflicted receipts and customer outcomes stay unknown. Real-provider acceptance, independent business outcome verification, agent uplift and customer pilots remain open.

## Acceptance

`tests/test_open_work_case_declarations.py` covers default-OFF behavior, prior review admission, scope separation, exact-version CAS including concurrent writers, dependency cycles and same-scope checks, multi-hop review removal without silent dependency laundering, disconnect erasure, retention reduction and session-only scrubbing, review-expiry/re-review resurrection prevention, transitive prerequisite expiry, write-time cascade pruning after a shortened expiry and non-renewing CAS edits, storage symlink/permissions, rollback and absence of native/customer content. Synthetic early re-review regressions cover an A←B←C approval chain, preservation of an unrelated D, stale CAS rejection after re-review, explicit fresh re-declaration, and a review replacement between two save admission snapshots. Existing expiry regressions now use a stable original review window rather than silently treating an early re-review as continuing approval. Two additional synthetic CAS regressions verify A←B←C transitive invalidation on an exact prerequisite revision, unrelated-case survival, stale dependent CAS rejection, failed cyclic-update rollback and same-value revision invalidation. Two synthetic interrupted-ledger/scope-mismatch regressions remove a prerequisite row or alter its stored scope, verify multi-hop dependents remain hidden, then trigger write-time pruning and verify the old dependent revisions cannot reappear. This guards a local auxiliary-store integrity failure, not cryptographic tamper resistance or atomic cross-process source-grant fencing. Existing workflow mapping, Activity, Discovery Mode, REST/MCP, export and provider readers are untouched. These are synthetic/local tests, **not** real-tenant or customer-outcome validation.


## Temporal provenance and as-of reads

A declaration is an owner approval recorded at its exact `created_at` and `updated_at`, **not** a historical source fact. When a trusted caller supplies an as-of time, the bounded list omits any declaration created or revised after that instant, even if its human review was already active. It also closes dependency availability transitively, so a dependent cannot point to a hidden future-approved prerequisite. Because the ledger retains only the current revision, an earlier as-of read **does not reconstruct a previous version**: it conservatively omits the later-revised case rather than falsely presenting future approval as historical evidence.

A successful exact-CAS edit cannot move `updated_at` earlier than the existing creation or last revision, and a dependent cannot be approved at a time before its prerequisite's creation or latest owner-declared revision. These checks use the existing explicit timestamps and SQLite transaction; they do not infer a business obligation, grant provider access, or supply a cryptographic clock. Caller-supplied timestamps still require a trusted clock and cannot substitute for an atomic cross-process revocation fence. Synthetic regressions cover future-created approvals, future revisions, rejected backdated edits and prerequisites, and unaffected independent cases.

## Incarnation-bound optimistic concurrency (ABA protection)

A revision number can reset to 1 when a reviewed case is explicitly deleted, expires, is pruned as a dependent, or is re-declared. **Revision-only edits are therefore refused**, even when the number matches. Each fresh declaration gets an independent cryptographically random 128-bit `edit_token`; a successful CAS update preserves that token while incrementing the revision. A newly declared incarnation gets a new token. The trusted caller must supply `expected_revision` **and** `expected_edit_token` from the same previously returned declaration. Both are compared under the SQLite write transaction; a stale caller cannot overwrite a newly declared B rev1 using an earlier B rev1 token. The token is a concurrency guard, **not** a login credential, provider grant, approval, or authority to execute. Missing, malformed or mismatched tokens fail closed. No erased declaration content, tombstones, customer fields or native IDs are retained to prevent ABA.

This draft-only internal ledger upgrades a legacy v1 SQLite file transactionally to schema version 2, assigning new random edit tokens to surviving rows. Existing callers holding only a v1 integer revision must re-read the declaration before editing; they cannot bypass the new guard. This migration neither changes a review, extends expiry nor creates an obligation. A read of an existing legacy database can perform this local schema upgrade; a read with no database still creates nothing. No REST/MCP/dashboard contract currently exposes this draft-only writer. Deterministic synthetic regressions cover prerequisite-edit → dependent-purge → re-declare → stale old-CAS, explicit review-delete/re-declare, missing-token refusal, and v1 migration. This does not solve cross-process source-revocation fencing or equal-timestamp human review generation.


## Corrupt auxiliary dependency graphs (synthetic fail-closed behavior)

Persisted dependency edges must decode to a JSON **array** of at most eight unique opaque `owg:w:` refs, with no self-edge. A JSON object, scalar, malformed JSON, duplicate, or invalid reference is **not** an empty dependency list. Read projections withhold malformed declarations and all their dependent cases rather than silently converting a formerly dependent approval into an independent one. The bounded read and write-time availability closures grow only from eligible dependency-free roots, so a damaged persisted A↔B cycle cannot be presented as an approved dependency graph. A subsequent explicit declaration save transactionally prunes invalid/cyclic rows and their dependents while retaining unrelated current reviewed declarations. Explicit review deletion remains possible even if the auxiliary graph is damaged: its existing fallback erases the entire declaration ledger if safe targeted purge fails.

The normal trusted writer already rejects invalid dependency shapes and cycles. These tests simulate damaged **local auxiliary SQLite state**; they are not evidence of cryptographic database tamper resistance, multi-process source-grant fencing, live provider validation, or independently verified business outcomes. No provider calls, agent permissions, background execution, REST/MCP routes, or additional stored customer data are introduced.

## Damaged auxiliary approval clocks (synthetic fail-closed behavior)

Only canonical timezone-aware UTC ISO-8601 timestamps emitted by the trusted writer are valid stored approval clocks. Read-time visibility and transactional write-time pruning independently validate the stored creation, latest revision and expiry timestamps against the current as-of time, admitting human review, and retained-history TTL. A malformed/noncanonical clock, an approval revision before creation or after the as-of time, or a deadline extending beyond the review or retention window cannot become an owner-approved declaration. Its explicitly dependent closure is withheld from reads and erased on the next authorized declaration write; unrelated reviewed cases remain. This prevents a damaged auxiliary row from crashing normal list/prune operations or laundering future/over-retained approval into current authority. It is conservative: malformed rows are not repaired or treated as independent obligations.

Eleven parameterized synthetic corruption regressions cover invalid, future, stale, noncanonical-offset and contradictory timestamps, multi-hop dependency suppression, non-mutating reads and subsequent transactional cleanup. This does **not** establish protection against arbitrary SQLite tampering, trustworthy host clocks, atomic cross-process grant revocation, real provider acceptance or verified customer outcomes. No source reads, routes, new privileges or autonomous actions are added.


## Damaged owner-declaration metadata (synthetic fail-closed behavior)

The v2 auxiliary SQLite ledger also validates **stored** opaque work/scope refs,
fixed owner-declared goal and expected-outcome codes, positive bounded integer
revisions, and lowercase 128-bit incarnation edit tokens. SQLite's dynamic
column typing and damaged local rows must not make unsupported codes or
unusable versions appear to be approved owner declarations. Read-time
availability withholds invalid rows and their entire dependent closure;
the next explicitly authorized save transactionally prunes that closure while
preserving unrelated valid approvals. This does not infer obligations or
source/customer outcomes, grant authority, or add a new API.

Ten parameterized synthetic corrupt-row regressions cover both codes, invalid
integer/text revisions, tokens and opaque identities, with A <- B <- C
transitive suppression, unrelated D survival, and post-prune stale CAS
refusal. They do not prove resistance to malicious database tampering,
cross-process source revocation, real provider acceptance, or verified
business outcomes.

## Read-time consent and retention revalidation (bounded race narrowing)

A declaration list now rechecks local human-review admission windows and history
retention **after** fetching its bounded SQLite rows and closing the connection.
A removed review or a replacement review with a different creation/expiry
window suppresses that declaration and every transitively dependent approval;
an unchanged independent declaration remains available. If retention shortens,
the stricter of the two snapshots governs the response, including refusing
previously saved deadlines that exceed the new retention window rather than
silently extending or reapproving them. If durable retention is
disabled during the read, no owner facts are returned and the local auxiliary
ledger is erased. This does not grant new source reads or authorize actions.

These are deterministic synthetic interleavings, **not** proof of atomic
cross-process revocation: a grant may change again after the final check, and
the review store and declaration store do not share a transaction. An equal-
timestamp review replacement still cannot be distinguished by the current
review-window representation. Trusted callers must not treat a prior list
response as a durable provider grant, business obligation, or verified outcome.


## Explicit dependency-edge approval windows

The local ledger now requires **each already declared dependency edge** to be
temporally consistent in addition to validating each row independently. A
dependent's latest owner approval must not predate its prerequisite's latest
owner approval, and the dependent's deadline must not extend past the
prerequisite's deadline. Both conditions compare parsed canonical UTC instants,
not lexical timestamp strings. This is **not** a time-only join or inferred
business obligation: only existing exact, same-scope owner-declared references
are evaluated. An invalid prerequisite edge withholds its dependent and the
entire transitive dependent closure in non-mutating reads. The next authorized
write prunes that closure transactionally, without deleting unrelated reviewed
cases or extending approvals.

Two synthetic SQLite corruption regressions start with A <- B <- C and an
independent D, then make either A's latest approval newer than B's approval
or B's deadline later than A's. Every individual row still passes its own
temporal and human-review window checks. The list must expose only A and D;
a subsequent explicit edit of D prunes B and C, and stale B CAS cannot revive
them. These tests do not prove arbitrary database-tamper resistance, atomic
source-grant revocation, real provider/customer outcome verification, or
business uplift. The ledger remains internal-only, local, opt-in, and
non-executing.


## Final pre-commit review and retention check (bounded revocation window)

A declaration save now takes a **third** local human-review and retained-history
policy snapshot after its SQLite row insert/update and before COMMIT. If the
admitting review or any newly declared prerequisite review was removed or
replaced, or if the retained-history duration changed (including session-only
revocation), the entire transaction rolls back. The saved row, its revision,
and any transitive dependent-pruning side effects are not committed. This
protects against a revocation observed after the earlier BEGIN IMMEDIATE
admission check; it does not extend an approval or grant provider access.

Seven parameterized synthetic regressions cover create/update/dependency
review revocation and create/update retention reduction or disabling at that
exact boundary. They check the underlying SQLite rows after rollback, not
only a read-time suppression filter. This is **not** atomic cross-process
source-grant fencing: a review or grant may change immediately after the final
check, and the review/policy stores do not share this SQLite transaction.
Equal-timestamp replacement review generations remain indistinguishable.
Do not expose the internal ledger as an independent provider permission,
business obligation, verified outcome or agent execution authorization.


### Persisted row-count integrity

The local auxiliary ledger is capped at **50 total declarations across all source scopes**. If damaged/legacy SQLite content exceeds this bound, scoped reads withhold the entire owner-approval projection instead of presenting a seemingly complete truncated subset. Transactional pruning and dependent-deletion traversal fetch at most 51 rows and refuse an over-capacity graph. A failed save rolls back; explicit review deletion can scrub the corrupt auxiliary ledger through the existing fail-closed cleanup path. Exactly 50 rows remain supported, with unreviewed entries removed by the next authorized save. This is a local resource/availability bound, not tamper-proof storage, tenant authorization or independently verified outcomes.


### Bounded legacy schema-v1 migration

An existing local schema-v1 declaration store is upgraded on first access to
schema v2 with per-incarnation edit tokens. Migration now reads at most **51
legacy references**, and refuses an over-capacity (>50) store **before**
altering its table or generating any tokens. The SQLite migration transaction
rolls back and leaves the v1 version, columns and rows unchanged on overflow;
it never silently treats a partial migration as approved owner declarations.
Exactly 50 legacy rows can migrate with distinct opaque edit tokens. These
are synthetic boundary tests, not evidence of live source authority, a
customer-verified outcome, or cross-process grant fencing.

### Persisted dependency JSON parsing bound

The trusted owner-declaration writer emits a JSON array of at most eight
opaque case references (at most 336 characters with its current serializer).
The auxiliary SQLite reader now refuses a stored `dependencies_json` value
longer than **1,024 characters before calling the JSON decoder**. This
conservatively permits modest legacy whitespace/serialization differences
without allowing an oversized or deeply nested damaged payload to consume
unbounded JSON parsing work. Non-text, malformed and oversized values are
invalid dependency graphs, **not** empty dependency lists. Affected cases and
all their declared dependent cases are withheld on non-mutating reads and
pruned by the next authorized save. Explicit review deletion retains the
existing whole-ledger scrub fallback if targeted deletion cannot safely
interpret the damaged auxiliary graph.

Three synthetic regressions cover valid eight-edge writer output and the
1,024/1,025-character boundary with a decoder tripwire; transitive withholding
and authorized transactional pruning; and explicit review deletion with an
oversized malformed payload. This caps **JSON decoding work**, not the memory
required for SQLite to return a corrupted oversized cell or arbitrary local
database tampering. No provider authorization, business status, customer
outcome, source receipt or agent execution permission is inferred.


## Bounded legacy-v1 migration identifiers

The original v1 ledger is an ordinary SQLite rowid table with a `TEXT PRIMARY KEY` opaque work-item reference. Its v2 incarnation-token upgrade first fetches at most 51 **integer rowids** (50 allowed plus an overflow sentinel) and updates tokens by `rowid`. It does not materialize or rebind arbitrarily large native `work_item_ref` TEXT values from a damaged legacy database merely to perform migration. More than 50 legacy rows still fail closed before any schema alteration or token generation; an exactly 50-row valid ledger remains supported. A synthetic malformed 256-KiB reference regression inspects the migration SQL and verifies both surviving rows receive distinct tokens without changing their original references.

This bounds migration *identifier enumeration*, not SQLite file size, page-cache allocation, later full-row reads, or a malicious local database. The normal reader still validates opaque reference syntax and excludes malformed rows; it does not turn a malformed ref into a verified business fact. No new source authority, tenant-wide revocation guarantee or independently verified outcome follows from this change.


## Persisted approval timestamp parser bound

The internal writer emits canonical UTC ISO-8601 owner-approval timestamps of
at most 32 characters. A damaged auxiliary SQLite row with a non-text or
more-than-40-character `created_at`, `updated_at` or `expires_at` is now
rejected **before calling Python's ISO timestamp parser**. Existing canonical
UTC, review-window, retention, and dependency-approval checks remain
mandatory. A rejected prerequisite withholds its entire explicitly declared
dependent chain; a subsequent authorized owner save prunes that chain while
preserving unrelated reviewed declarations. It never converts a malformed
clock into a new approval or source-confirmed outcome.

Synthetic regressions verify a parser tripwire on oversized/non-text values,
plus 256-KiB corruption of each of the three timestamp columns, read-time
withholding, and transactional pruning. This only bounds **Python timestamp
parsing**, not SQLite's prior materialization of an oversized TEXT cell,
filesystem growth, atomic cross-process source revocation, or real provider
and customer-outcome validation. No routes, agent permissions or source reads
are added.


### Damaged SQLite reference storage classes

SQLite's `TEXT` affinity does not prevent a damaged auxiliary row from holding a `BLOB` work-item key; numeric values may also be stored as invalid text. These keys are never valid `owg:w:` identities and cannot become owner-approved declarations. Transactional pruning deletes ineligible rows without sorting mixed Python `str`/`bytes` keys, so one corrupt BLOB cannot abort a separate authorized save. Explicit dependent declarations referencing a missing or corrupted prerequisite are pruned transitively, not silently reclassified as independent obligations. Synthetic regression tests exercise both BLOB and numeric-derived invalid keys, dependent withholding and unaffected declarations. This does **not** make the SQLite database tamper-resistant, prevent prior cell materialization, or provide cross-process source-revocation fencing; no provider/customer outcomes are verified.


### Explicit review deletion with mixed SQLite key classes

The same malformed BLOB/TEXT key hazard also affected the explicit
`purge_case_declaration` path. A corrupt BLOB row whose declared dependencies
include a revoked reviewed case could enter the deletion closure alongside
valid string references. Sorting those mixed Python keys raised `TypeError`,
causing the existing review-deletion fallback to scrub the **entire**
auxiliary ledger, including unrelated, still-reviewed owner declarations.
The bounded closure is now deleted without sorting. The synthetic SQLite
regression exercises the real `remove_review_intention` lifecycle: revoked
root, valid dependent, and corrupt BLOB dependent are removed while an
independent approved declaration remains. The primary review deletion still
succeeds. Invalid rows are never treated as approved owner facts.

This is a narrow preservation and minimization guarantee for an auxiliary
local ledger, not a repair of arbitrary SQLite corruption or a change to the
existing fail-closed full scrub when a dependency graph cannot be decoded.
It does not add source authority, cross-process revocation fencing, provider
or customer outcome verification, or any agent execution permission.


## Unversioned auxiliary-store admission

A newly created or existing **empty** SQLite file at schema version zero may be initialized to the supported v2 declaration table by an explicit, reviewed save. A version-zero file that already contains any user-defined table, view, index or trigger is **not** adopted: the save fails closed and rolls back without stamping `user_version=2` or modifying the existing schema. This prevents `CREATE TABLE IF NOT EXISTS` from silently treating an incompatible or unrelated pre-existing `declarations` table as a valid durable approval ledger. A read of an unsupported version also fails closed rather than interpreting its contents as approved facts.

The explicit bounded v1-to-v2 migration remains separate and unchanged. This guard does not authenticate the local filesystem, repair corrupt databases, prove cross-process grant revocation, or verify provider/customer outcomes. Synthetic SQLite tests cover incompatible declaration tables, unrelated tables/views, unchanged version/data on rejection, and initialization of a clean version-zero file.


## Version-two schema identity admission

An auxiliary SQLite file stamped `PRAGMA user_version=2` is **not** automatically
a valid declaration ledger. Before reading or writing owner-approved facts,
the internal store checks the fixed ten-column v2 table contract: exact
column names/order, declared SQLite types, required NOT NULL fields, and
`work_item_ref TEXT PRIMARY KEY`. A missing primary key could otherwise
allow two rows with the same opaque case identity, making exact optimistic
CAS and deletion ambiguous. Missing/extra columns, a wrong revision type,
or a nullable required scope also fail closed without attempting to repair,
delete or adopt that incompatible table.

Both freshly initialized v2 stores and the supported bounded v1-to-v2
migration retain their existing behavior; v1's default on the newly added
`edit_token` column is permitted because it does not change identity or
CAS requirements. Five synthetic SQLite regressions exercise malformed
v2 schemas, including two duplicate case rows in the no-PK variant, and
verify that reads/saves refuse them while preserving the stored schema,
rows and version. Existing clean initialization and v1 migration tests
cover compatibility.

This is a narrow structural admission check, **not** filesystem tamper
resistance, SQLite integrity verification, a cross-process source-revocation
fence, or proof of provider/customer outcomes. It grants no source reads,
new business obligations, agent rights or execution.


## Legacy v1 schema admission before migration

A local auxiliary file claiming `PRAGMA user_version=1` is not automatically a
valid legacy declaration ledger. Before enumerating rows, generating incarnation
tokens, or running `ALTER TABLE`, the migration now checks that
`declarations` is a table with the exact nine original v1 columns, their
declared SQLite types, required NOT NULL fields, and the opaque
`work_item_ref TEXT PRIMARY KEY`. A marker on a view, a table without that
primary key, or a table with missing/extra/wrongly typed or nullable fields
fails closed. Rejection rolls back without stamping v2, modifying existing
rows, or generating tokens. The normal bounded 50-row v1 migration remains
supported, and the existing v2 shape preflight still runs after migration.

Six synthetic SQLite regressions exercise malformed legacy schemas, including
duplicate opaque identities without a primary key, and verify that both reads
and writes refuse the ledger while preserving its version, schema, and rows.
This guards schema identity only; it does not authenticate the filesystem,
repair arbitrary SQLite corruption, establish cross-process revocation
atomicity, verify source/customer outcomes, or grant agent execution.


## Versioned schema-object admission (v1 migration and v2 reads/writes)

Exact column and primary-key checks do **not** detect additional SQLite
triggers. A pre-existing `AFTER UPDATE` trigger can erase or rewrite rows
when v1 migration assigns incarnation tokens; an `AFTER INSERT` or
`AFTER UPDATE` trigger can mutate owner facts during an otherwise reviewed
v2 save. Neither effect is an independently approved declaration.

Before migrating a v1 ledger or exposing/editing a v2 ledger, the local store
now refuses **any user-defined schema object beyond the one
`declarations` table**. This includes foreign tables, views, explicit indexes
and executable triggers. SQLite's implicit `sqlite_autoindex_*` for the
`work_item_ref TEXT PRIMARY KEY` is permitted. Rejected stores retain their
schema, rows and `user_version`; there is no automatic repair or migration
of unrecognized objects. Ten parameterized synthetic SQLite regressions
exercise both versions and five extra-object forms, including INSERT/UPDATE
triggers, while existing clean v1/v2 compatibility tests remain applicable.

This is a narrow fail-closed schema admission guard, not cryptographic
tamper resistance, `PRAGMA integrity_check`, protection from a concurrently
malicious local process, or atomic source-grant revocation. The ledger remains
internal-only and disabled by default; no provider reads, obligations, agent
permissions, execution or verified customer outcomes are introduced.


## Generated-column schema admission

SQLite's `PRAGMA table_info` excludes generated/hidden columns. An otherwise
exact nine-column v1 or ten-column v2 `declarations` table can therefore carry
an additional `GENERATED ALWAYS AS ... VIRTUAL` or `STORED` column without
appearing different to `table_info`, and without creating an extra user-defined
`sqlite_master` object. A version marker and visible-column match do not
establish the private ledger's exact identity.

Both v1 migration preflight and v2 read/write admission now use
`PRAGMA table_xinfo` and require **exactly** the trusted columns with
`hidden=0` for every field. Extra generated/hidden fields fail closed before
migration or owner fact exposure, even if the visible fields, primary key and
schema-object list otherwise match. Four synthetic real-SQLite regressions
cover v1/v2 × VIRTUAL/STORED, checking rejection before token generation,
version changes or row mutation. Existing clean v1 migration and v2 operation
tests remain the compatibility baseline.

This is **schema-shape** validation, not a full SQL-DDL equivalence proof:
arbitrary CHECK constraints, malicious concurrent filesystem writes, and
cross-process source-grant revocation remain outside this guard. No new
provider reads, agent rights, background execution, or business outcome
verification are implied.


## Hidden UNIQUE and foreign-key schema constraints

A versioned SQLite table can have exactly the expected columns, types and
primary key while still changing owner-declaration behavior. A table-level
`UNIQUE(scope_ref)` creates an extra **implicit** `sqlite_autoindex_*` that
was previously excluded from the user-defined schema-object check. A foreign
key, including `ON DELETE CASCADE`, creates no additional schema object at
all. Either can make otherwise valid owner saves fail or delete unrelated
approved declarations without a reviewed dependency change.

Both v1 migration and v2 read/write admission now require the exact one
implicit `work_item_ref` primary-key index reported by
`PRAGMA index_list(declarations)`, with no additional UNIQUE indexes, and
an empty `PRAGMA foreign_key_list(declarations)`. Four parameterized
synthetic SQLite regressions cover v1/v2 × extra UNIQUE/foreign-key schema,
verifying rejection before migration or mutation and preservation of rows,
schema and version. Existing clean v1 migration and fresh-v2 tests remain
required for compatibility.

This is bounded structural admission, **not** a complete SQL DDL equivalence
proof (for example, arbitrary CHECK constraints and collations remain outside
this guard), database tamper resistance, atomic source-grant revocation,
provider/customer outcome verification or execution authority. The store
remains internal-only, local, and disabled by default.
