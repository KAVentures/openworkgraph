# Separately shared analysis cases

Shared aggregate analysis access alone never grants case access. This bounded M2/R4 path adds
an explicitly prepared case dataset to one exact aggregate revision. It is a publisher-supplied
case disclosure, not a live log connection, independent source verification or execution grant.

## Prepare and attach

1. In the local event-log analysis builder, run a **case-level** analysis and choose
   **Prepare case sharing files**. Currently cohort filters are supported; analysis-specific
   case filters and activity filters are refused rather than silently changing the population.
   Metric definitions may be used by the aggregate but are not included in the case file.
2. Review both previews. The aggregate retains its existing separate consent/download.
   The case preview lists every complete coded timeline and requires its own confirmation.
   Download both files. Preparation and download send nothing to a Gateway and create no new
   local export store. A dashboard session and a separate case-preview intent header are required;
   AI bearer tokens cannot prepare these disclosures.
3. Publish the paired aggregate in `/analyses`, then open that exact revision. With explicit
   case-read/case-write scopes, its owner can choose the case file, confirm the separate
   case-level disclosure and attach it. The browser rejects unexpected fields before transfer;
   the Gateway independently validates it. Source/spec fingerprints and complete case count
   must match the aggregate. Those fingerprints remain publisher claims at the Gateway, not
   proof of ownership, completeness, business intent or source truth.
4. Add aggregate members using the existing Access controls. Then, under **Case viewers**, list
   the people allowed to inspect this revision's cases. They also need the case-read scope.
   The case-viewer list is a complete replacement and requires explicit confirmation.
5. Authorized people can filter whole cases by coded activity or exact variant, inspect a coded
   timeline, browse case pages, inspect observed transitions/variants and download an audited
   JSON export. Queries use only this bounded immutable dataset. They cannot join private logs,
   read another attachment or derive counts from an inaccessible dataset.

## What is disclosed

`openworkgraph.analysis-case-dataset.v1` contains 1–500 complete cases, at most5,000 events and
100 activity codes, with at most512 KiB of canonical UTF-8 JSON. Cases are coded C1… in export
order. Activities are coded A1… in first-occurrence order. No original case/event/activity IDs,
activity labels, people, resources, attribute values, filter definitions or original event dates
are included; their dictionaries are never exported. Each case starts at zero elapsed
microseconds and retains its full ordered sequence, including repeated activities and tied
elapsed times. Elapsed values are exact browser-safe integers, bounded to100 years. Export
refuses oversize/empty populations; it does not sample cases, cut long traces or report partial
counts as complete. Full-source fingerprints retain the existing30M-row budget and algorithm
(events, stored objects and links, not temporal attribute history).

**Codes are not anonymization.** Topology, recurrence, durations, rare cases, counts, the parent
aggregate's values/units/title and generation metadata can identify people or business facts.
Codes mean something only inside this dataset; they are not stable identities across files or
revisions. There is no group-row-to-case mapping, absolute event clock, cross-case timing or
employee-effort measurement. Dataset generation time is metadata, not an event time.
Within-case gaps include elapsed waiting; they do not establish processing time or causality.
Case times retain microsecond precision; existing aggregate metrics retain their own declared
basis and precision. Nothing infers a business association across employees or source systems.

The server computes exact selected-case/event/rework counts and median elapsed duration.
Transitions show the top100 types and explicitly report truncation; variants show the top20
and report the total distinct-variant count. Case lists are independently paginated (up to100
per request); a timeline contains all events in its case. Filters select whole cases, so no
filtered timeline is a misleading prefix. Query inputs are fixed typed codes/digests, not SQL,
Python expressions or arbitrary uploaded programs. There is no background job/cancel API.

## Permissions and immutable revisions

Administrators must separately configure `org:analysis:cases:read` and
`org:analysis:cases:write` in the existing OIDC group map. Neither is enabled by default.
These remain analysis scopes in delegated tokens or the existing opt-in company analysis
session; process sessions, service tokens and evidence permissions still cannot access them.
The normal analysis-read prerequisite also applies. Case read requires both a current aggregate
membership (or its existing admin access) and a separately current case-viewer grant, except
that the publication owner can read with the explicit case-read scope. Aggregate administrators
have no automatic case grant, even if they have case scopes. Only the actual publication owner,
with analysis-write plus both case scopes, can attach, grant or delete case datasets. Aggregate
editors and administrators cannot broaden the owner's case disclosure.

Each case attachment and grant is pinned to one numeric analysis revision. New analysis revisions
do not inherit case data or case grants. An old grant permits only its explicitly shared old
revision while the parent remains available. **Every parent access revision change** (including
retention renewal or resaving the same member list) invalidates all prior case-viewer grants.
The owner must explicitly reconfirm each complete case-viewer list. Removal/re-addition of an
aggregate member therefore cannot restore old case access accidentally. Requests recheck current
parent authorization and grant revision under the same parent-row lock as access changes;
SQLite uses an immediate transaction. Stale case/access counters refuse writes.

An attachment cannot be overwritten. The owner may delete it with exact digest/access counters;
its payload and grants are removed, while a small digest/metadata tombstone prevents reattaching
different data to the same parent revision. A new aggregate revision is required for new case data.
Corrupt case data refuses reads/exports; owner metadata/deletion remains available to erase it.
There are at most20 active attachments per organization. Admission uses the parent organization
lock on both supported databases. Capacity refuses new writes; it never prunes history silently.

Attachments inherit the parent's expiry and cannot extend it. Parent deletion and periodic expiry
cleanup erase every case payload, tombstone and grant in the same database transaction. Expired
parents fail access immediately even when physical cleanup is delayed. Existing audit entries,
backups and previously downloaded copies follow their separate policies. Audit records contain
view/revision/digest/action/count metadata, never the case bodies, original fields or filter values.
Clear/disconnect removes case tables, file previews, grants and timelines; stale selection/identity
responses cannot repopulate them. Network failures cannot recall copies already served.
Before downgrading to a version without case cleanup support, remove case attachments with the
current version or retain its cleanup worker; an old binary does not understand these new tables.

## Interfaces and limits of acceptance

Local: `POST /v1/event-logs/{id}/case-sharing-preview`, normal typed analysis spec,
`X-OWG-Case-Sharing-Preview: true`, dashboard session. It returns the paired aggregate and case
packet from a single source lock, guarding against append/delete between them.

Gateway, beneath `/v1/human/analyses/{view_id}/case-data`:

- `POST`: owner attaches to exact current revision, parent hash/access counter and confirmation.
- `GET ?revision=N&activity=A1&variant=<digest>&after=<number>&limit=50`: authorized bounded analysis.
- `GET /cases/C1?revision=N`: full coded case timeline.
- `GET|POST /access`: exact revision case-viewer list and CAS replacement.
- `GET /export?revision=N`: fresh authorization, atomic audit, JSON download.
- `POST /delete`: confirmed payload/grant erasure with exact counters.

Request bodies are bounded (640 KiB for attach), duplicate keys and unknown fields are refused,
and all data responses use no-store. SQLite/PostgreSQL tests cover authorization, concurrency,
rollback, retention and parent lifecycle; owned browser fixtures cover actual paired exports,
publication/grants/filters/timelines/export/revoke/delete and WCAG/mobile. Synthetic identities
and owned logs are not a real-company identity/pilot acceptance. General tenant raw-log fields,
ad-hoc shared queries, cancellable jobs, shared signals/pipeline editing, live source freshness,
real providers and broader M2/competitor parity remain open.
