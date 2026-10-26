# Shared case analyses and snapshot checks

The separately granted [case workspace](SHARED_ANALYSIS_CASES.md) supports typed ad-hoc queries
and shared, versioned analysis definitions. Every operation uses one exact immutable coded case
dataset. It does not query a live business system, refresh from a private log, run uploaded code,
start background monitoring, send notifications or grant execution permission.

## Use the workspace

Open a shared analysis revision whose case dataset you may read. Under **Shared analyses and
snapshot checks**, choose whole-case filters, grouping and up to four measures, then **Run shared
analysis**. A query does not need write permission and does not persist its result. The table
shows selected counts, units, missing-value coverage and any bounded result truncation.

Optional threshold checks apply separately to each returned group. A complete numeric result
reports `passes` or `fails`; missing measurements report `incomplete`, and a null result reports
`not_evaluable`. A partial mean/sum therefore cannot silently become a passing check. The response
also says how many groups were not returned. There is no overall verdict across hidden groups,
no business-policy approval, live monitoring, independent outcome verification or authority to act.
Thresholds are entered in the displayed measure unit; equality uses the numeric values without
an implied tolerance or currency/unit conversion.

With analysis-write scope and an editor/owner/admin role on the parent analysis, **Save shared
analysis** publishes an explicitly reviewed name and definition for the current case readers.
A case grant is still required; aggregate editor/admin permissions do not substitute for one.
A case viewer can run/read/export analyses but cannot save just because their company token
has analysis-write. Metadata editors do not need raw case-write permission. Only the actual
parent owner, with case-read and analysis-write permission, may delete all saved revisions.

Names, authorship identifiers, definitions and retained history are visible to every currently
or subsequently granted reader of this exact case dataset. Avoid sensitive names unless you
are authorized to share them. The case-viewer confirmation describes this retained history.
New parent analysis revisions receive no definitions, case data or case grants automatically.
Use **Copy form as new analysis** to reuse current settings under a new ID/name.
Historical definitions can be opened and exported; the UI refuses saving over a historical
revision. Editing a form also disables saved-definition export until a saved revision is reopened,
so the download cannot silently differ from the displayed form. To change it, open the current revision and explicitly save a new immutable revision.

## Query contract and meaning

`openworkgraph.shared-case-analysis.v1` contains fixed typed fields, never SQL or expressions:

- Whole-case filters: contains coded activity, exact variant digest, inclusive minimum/maximum
  duration in seconds, minimum events, and whether a case repeats an activity.
- Grouping: all selected cases, first activity, last activity, exact variant, or repeated activity.
  Each selected case belongs to exactly one group. Variant identifiers hash the ordered coded
  path; they are meaningful only inside this case dataset.
- 1–4 measures: case count/share, or sum/mean/median/p90/min/max of elapsed case seconds,
  event count, occurrences of a coded activity, or elapsed seconds between two coded activities.
- Time between uses the first `from` occurrence and the first later `to` occurrence in event
  order. Equal activity selectors require another occurrence. Missing endpoints produce null,
  not zero. Tied timestamps can produce a zero gap; ordering does not establish causality.
- Shares use **all selected cases** as their denominator, even when only some result groups
  fit the limit. Empty ungrouped queries report count0, null share and null numeric aggregates.
- Median/p90 use linear interpolation at `(n−1) × quantile`, matching continuous quantiles.
  Sum/mean/min/max use the available values with coverage disclosed; absent measurements are
  not imputed. A threshold cannot pass/fail if its measure has missing cases in that group.
- Group limit1–100, with explicit total-group count and truncation. Ordering is by the first
  measure descending with a stable group tie-break, or by group code. A snapshot check identifies
  one of the existing measures, a finite browser-safe threshold, and a fixed comparison.

All inputs are validated independently by the server. Booleans do not count as numeric values;
nonfinite/unsafe values, unknown fields, bad selectors and malformed intervals are refused.
The form rejects invalid numbers before JSON serialization can turn them into null. Original
case/activity/person fields, timestamps, attributes and external resources remain unavailable.
Within-case elapsed gaps are not employee effort. No cross-source or cross-case associations
are inferred. Results remain publisher-supplied, not independently verified source evidence.

The existing dataset limits remain500 complete cases/5,000 events/100 coded activities/512KiB.
The query work is synchronous and bounded by that packet and four measures. This is not a
large-data, concurrent multi-user performance certification or a background job/cancel API.

## Sharing, revisions and lifecycle

A saved analysis binds its normalized definition, name and exact case-dataset digest in a
canonical content hash. Its ID is scoped by tenant, parent view, parent revision and dataset.
Saving requires the current dataset digest, parent access counter, case access counter and
saved-analysis revision. A changed audience or a competing write refuses the save; no definition
is silently published to a newly changed reader set. There are at most20 saved analyses per
case dataset and10 immutable revisions each. Capacity refuses new writes and never prunes old
history. The digest detects stored corruption; it is not a public signature or source certificate.

Current parent membership, case grants, company scopes and parent expiry are rechecked on
**every** list/read/evaluate/save/export/delete request. Parent access changes invalidate case
grants as before. Revoked readers cannot fetch old definitions or results. Metadata writers
cannot regrant themselves case access or replace case data. Saved results are recomputed from
the same authorized immutable packet on open/export, not from the viewer's local logs.

Case deletion clears every dependent definition and revision in the same database transaction.
Parent deletion/expiry does the same, including the case tombstone and grants. Owner-only query
history deletion removes only that definition's revisions and leaves the case/aggregate intact.
Corrupt definitions refuse execution/export; the list supplies only maintenance IDs/revisions
for those entries so the owner can erase them without reading corrupt content. Valid historical
definitions remain independently readable even if a newer saved definition is corrupt.
Already downloaded copies and backups follow their separate policies; revocation cannot recall
previously served bytes. Closing or changing identity clears form values, names, history lists
and results; late responses cannot restore an earlier selection or identity.

Storage uses the existing Gateway database. The composite primary key supports tenant/dataset/ID
lookups; the parent-row lock serializes admission, CAS, access and cleanup on PostgreSQL, with
SQLite immediate transactions. Audit actions retain only IDs/revisions/digests and decisions,
not names, definitions, thresholds, coded case bodies, filter values or query results. Export
requires fresh authorization and an atomic audit. Audit failure rolls back saves/deletes.
Before downgrading, retain a version whose cleanup understands these new tables or delete the
shared case data using the current version.

## API

Under `/v1/human/analyses/{view_id}/case-data`, all data responses use no-store:

- `POST /query?parent_revision=N`: exact dataset digest, parent revision and typed spec; read-only.
- `GET /queries?parent_revision=N`: authorized latest definitions and current edit/delete flags.
- `POST /queries?parent_revision=N`: confirmed new saved definition with exact audience counters.
- `GET|POST /queries/{query_id}?parent_revision=N`: read latest or `revision=M`, or save a new revision.
- `GET /queries/{query_id}/export?parent_revision=N&revision=M`: definition plus snapshot result.
- `POST /queries/{query_id}/delete?parent_revision=N`: owner confirmation, dataset digest and exact
  latest revision; delete every saved revision of that definition.

Bodies are at most16KiB, duplicate JSON keys and unexpected fields are refused. Case authorization
preflight happens before parsing; the actual operation rechecks authorization under its transaction.
No new case scopes are enabled. Existing delegated-human/company analysis sessions are used;
process sessions and service tokens still do not grant access.

Acceptance uses owned case packets, independent arithmetic/quantile/missing-value oracles,
SQLite/PostgreSQL concurrency/rollback/lifecycle tests, and synthetic delegated browser identities.
It is not real-company identity/provider/pilot acceptance. General raw tenant datasets, long-running
jobs/cancellation, live shared signals/notifications, shared automation-pipeline governance and the
remaining #370 parity packages remain open.
