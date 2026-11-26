# Retained OCEL relationship snapshot comparison (R24)

From the local Evidence dashboard, open **Compare retained OCEL relationship
snapshots**. Choose two different retained OCEL JSON/XML logs. Inspect their
append revisions, exact relationship-set digests and object ID/type index
digests. Explicitly declare that both exports use a common object-ID namespace
before comparing. The declaration is not independent identity verification.

## Interpretation

The comparison is a literal union of directed (source ID, target ID, qualifier)
assertions. It neither merges logs nor infers new case membership. The result
partitions the retained union into mutually exclusive statuses:

- both: the assertion exists in both and the endpoint types agree;
- left_only / right_only: absent in the other retained set, with both endpoints
  present and same-typed in both indexes;
- left_endpoint_missing / right_endpoint_missing: one or both endpoint IDs
  absent in the indicated object index;
- endpoint_type_conflict: at least one common endpoint ID has a different
  type. This takes precedence over a missing endpoint or assertion presence.

Raw presence flags remain visible in the API for every row. Direction,
self-links, multiple qualifiers, empty qualifiers and whitespace qualifiers
are literal. Qualifier changes are distinct assertions, not edits of a
business relationship. The comparison does not establish source completeness,
validity at a time, actual additions/removals, real-world identity, or
execution authority. The two selected sides need not be chronological.

Optional filters match source/target type in either snapshot and an exact
qualifier. Whole-object-index counts are independent of assertion filters.
Status counts cover the entire filtered union before the status page filter.
The grouped view shows the first 100 groups, with truncation disclosed; type
labels prefer the left side in a conflicting group. Exact evidence rows
retain both types and are paginated in literal tuple order (1–200 per page).
Changing filters or selections clears the browser result.

## Local REST

Both require the dashboard session, not a general AI bearer, and use no-store:

- `GET /v1/event-logs/{log_id}/object-relationships/snapshot`
- `POST /v1/event-logs/{log_id}/object-relationships/compare`

POST accepts at most 16 KiB, with strict JSON fields:
```json
{
  "other_log_id": "log_0123456789abcdef",
  "same_namespace_declared": true,
  "left": {"revision": 0, "relationships_sha256": "...", "object_index_sha256": "..."},
  "right": {"revision": 0, "relationships_sha256": "...", "object_index_sha256": "..."},
  "qualifier": "",
  "status": "left_only",
  "offset": 0,
  "limit": 50
}
```
Replace the ellipses with the actual 64-character hashes returned by the
snapshot endpoints. Optional source_type and target_type are exact strings.
Omit qualifier to include all qualifiers; the empty string matches only
the empty qualifier. Omit status to include all statuses. Pass identical
pins and filters on later pages, using next_offset. The selection digest
binds both log IDs, pins and filters (not page size/offset). A changed append
revision, digest or deleted log refuses the comparison. The lock covers both
snapshot validations and the SQL query; no stale source is silently substituted.

The feature uses the existing local DuckDB object/relationship indexes.
Temporary comparison tables are not retained; no connector, cloud publication,
source-system calls, AI access grants or business actions are introduced.
Existing per-log OCEL bounds apply; full-set comparison and digest computation
may be expensive for large logs and no latency certification is claimed.

Synthetic fixtures exercise all statuses, symmetry, zero-assertion snapshots,
literal filtering, pagination, stale/deleted pins and authorization. Real
provider identity and temporal relationship validity remain external questions.

## Directed endpoint-pair comparison

Choose **Directed endpoint pairs (complete qualifier sets)**. The same pinned
endpoint accepts optional `mode: "pairs"` (default is `"assertions"`), exact
`source_id` and `target_id` filters in both modes, and optional `pair_status`
in pair mode. Pair mode refuses qualifier and assertion-status filters so it
cannot mistake a filtered subset for a complete qualifier set. Type filters
match either retained endpoint type as before. Every literal directed pair
is grouped once, including self-pairs and reversed directions. Endpoint type
conflict and missing endpoint statuses take precedence over set comparison.
Comparable pairs are partitioned into equal qualifier sets, different
qualifier sets, left-only pairs and right-only pairs. Counts and overlap
cover the filtered pair union before the pair-status page filter. Overlap is
shared comparable pairs divided by the comparable union; qualifier agreement
is equal sets divided by shared comparable pairs. Empty denominators return
null. Literal assertion counts include uncertain pairs and are separately
labelled. Exact per-pair counts show left/right/common and side-only qualifiers
without materializing unbounded qualifier arrays. **Inspect assertions**
reuses both pins and exact directed IDs, clears the qualifier/status filters,
and returns the original literal assertion evidence with existing pagination.
No inferred identity, actual relationship change, historical validity, or
execution authority is claimed. The SQL aggregation uses temporary tables and
existing OCEL limits; no new retained data. Full native/large-log performance
and browser verification remain necessary.
