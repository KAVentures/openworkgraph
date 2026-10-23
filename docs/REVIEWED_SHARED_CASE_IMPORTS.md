# Combine authorized logs through a reviewed shared case key

In **Evidence → Process mining · event logs**, expand **Combine authorized
exports using a shared case key**. Choose two to four authorized UTF-8 exports,
set each file's delimiter, and select **Inspect selected export columns**.
Inspection reads headers and row structure only in the browser. For each source,
choose its shared authoritative case key, structural activity label and timestamp.
Only unique exact OWG column-name matches are preselected; other fields require
explicit choices. Every unselected column is excluded.

Choose **Prepare shared-key import for review**. The review shows combined
case/event counts, source counts and how many cases have a key present in more
than one source. **Show local shared-case lookup** optionally displays the
private original key, its import alias and participating source aliases on
this device. Keys are quoted so whitespace/control characters are visible.
Hide the lookup to remove those values from the displayed page.

Confirm that the selected keys identify the same authoritative business case
namespace across the chosen exports. Equal-looking native ticket IDs from
unrelated systems or accounts are insufficient. Choose **Use reviewed
shared-key import for next analysis**, then **Analyze reviewed combined log**.
Selection, inspection, preparation, lookup, review and applying the import do
not upload a file or retain a snapshot. Explicit analysis sends only the
minimized structural log to the authenticated local OWG API.

## Identity and provenance

Shared keys match literally and case-sensitively. Whitespace and Unicode are
not normalized, and native identifiers are not hashed. Visually similar or
nearby keys remain separate unless their exact selected values agree. Empty or
whitespace-only keys are rejected. Every unmatched case and selected event is
preserved; timestamps, employee names, activity similarity and temporal
proximity never establish case equivalence.

The browser replaces native keys with `case_key_N` aliases and source origins
with `source_1` through `source_4` before upload. Filenames, account names,
unused columns, native keys and local lookup data stay in browser memory.
These aliases are scoped to this combined import, not an organization identity
or a verified anonymization guarantee. Sources must use compatible timestamp
semantics; dates remain offset-aware ISO-8601 values and are normalized to UTC
by the existing miner. Combined histories follow timestamps and supplied row
order for ties. Equal-time order remains unverified.

The explorer shows per-source event/distinct-case support, cases containing
multiple sources, the original input key alias beside each case ordinal, and
an origin alias on each event. Case ordinals reflect the sorted log; the input
key alias preserves the browser lookup identity even when order differs.
Activity nodes aggregate literal labels across exports; compatible labels must
come from the customer’s source/export workflow. Equal text does not verify
equivalent business intent. Repeated records and overlapping exports are not deduplicated without an
authoritative event identity. Existing duplicate and tied-time diagnostics
remain visible. A diagram arrow describes recorded adjacency, not causality,
a human handoff, business policy or verified waiting.

The namespace and origins are customer declarations. OWG does not independently
verify provider/account identity, source coverage, business case boundaries,
clock correctness, policy or outcomes through this import. It makes no source
API call and never joins these keys to observed desktop records, employee
identities, other retained logs or organization evidence automatically.

## Review, retention and reopening

Changing files, delimiters or mappings invalidates preparation, review and
pending transfers. A late inspection cannot restore an earlier file's headers.
Withdrawing shared-key review clears the lookup and stops future transfers
while preserving an unsaved structural draft. Keeping a selected file in the
browser remains separate from authorizing upload. Switching to another normal
CSV, retained snapshot or source projection clears the shared-import inputs and
native lookup. Review from an old import cannot clear a later independent view.

Retention remains a separate checkbox and **Save analyzed log locally** action.
Withdrawing/rechecking retention while a save is waiting for authentication
cancels that old request; saving requires another explicit click. A request
already sent under a valid grant cannot be unsent. Already saved snapshots use
their separate **Delete** action and existing expiry/retention scheduler.

Retained rows store only the bounded case-key and source-origin aliases.
Reopening, exact-revision structural comparison, whole-case filtering,
transition drill-down and complete case/event pagination preserve them. Cohort
metrics describe the selected complete cases, so a cohort may contain only one
of the original source aliases. Original alias values are never renumbered.
No source origin or native key is promoted into policy or execution authority.
Saving a process design, sharing it or generating an agent remains separately
reviewed through the existing catalog/designer.

## API and storage contract

The richer CSV header is `case_id,activity,timestamp,origin`, with optional
independently authorized participant/team columns. `case_id` must match
`case_key_1` through `case_key_1000`; `origin` must be one of `source_1` through
`source_4`. The legacy optional `source` column, if supplied by an API caller,
still discards its values. Ordinary single-log imports keep their existing
schema and behavior.

`POST /v1/process-mining/preview` and the existing retained-log POST require a
current dashboard session and literal
`X-OWG-Shared-Case-Keys-Reviewed: true` for origin metadata. Ordinary AI bearer
tokens cannot request it. This guard runs before private model lookup or body
reading. Retention additionally requires its existing independent confirmation
header; participant/team metadata additionally requires its own authorization.
Requests remain bounded to 1 MiB, 5,000 events, 1,000 cases and 64 activities
across the combined log, without truncating a source silently.

The normal preview format adds `case_correlation`, with the caller-reviewed
literal-key basis, per-origin support, multiple-source case count and explicit
false independent identity/origin verification flags. Cases add
`case_key_alias` and `origins`; events add `origin`. Retained datasets use nullable
`process_log_cases.case_key_alias` and `process_log_events.origin` columns in the
existing local store. Repeatable transactional upgrades preserve old rows with
nulls, rather than inventing provenance. The original six-field structural
parser contract remains unchanged for ordinary callers. Snapshot reconstruction
requires the saved alias provenance and never invents missing key associations.

The same dashboard-only queries and `Cache-Control: no-store` responses apply.
Deleting/expiring a dataset removes its aliases with the existing case/event
rows; no independent identity store or new synchronization mechanism exists.
The browser lookup is a frozen, non-enumerable property on the in-memory Blob
and is absent from its uploaded bytes and JSON serialization. No new capture,
AI/history access, model/provider request, business write or execution grant
is introduced.

Verification covers literal/Unicode/whitespace key identity, unmatched and
interleaved cases, source support, duplicate/tied records, private-field
minimization, bounds, independent grants, rejection before model reads, native
error-value exclusion, legacy migration, alias stability beyond preview caps,
full-history cohort reconstruction, expiry/deletion, stale inspections and
retention cancellation. A production browser/API/SQLite flow combines different
delimited exports, reviews one shared and two unmatched cases, verifies actual
received upload/retention bytes, reopens source-tagged histories, filters whole
cases, tests mobile layout and deletes the snapshot without creating canonical
observations.
