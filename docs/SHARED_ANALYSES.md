# Governed aggregate analysis snapshots

This is a connected first path for #370 M2/R4: local analysis → reviewed minimized file →
explicit Gateway publication → authorized shared view. It does not upload event logs, enable
organization evidence sync, expose personal evidence, run shared queries, or grant agents access.
The existing process-sharing and company evidence scopes remain separate.

## Publish and inspect

1. In the local event-log workspace, run an analysis and choose **Prepare sharing snapshot**.
   This recomputes it under the same workspace lock as its source fingerprint. It makes no
   network connection to a Gateway. A local dashboard session and explicit preview request
   are required; AI bearer tokens are refused.
2. Review the table, aggregation/value basis, units, coverage, generation time and hashes.
   Check the sharing confirmation, then download. Original group values become per-column
   codes; raw events, source/log/case identifiers, activity/person labels, filter values and
   metric definitions are omitted. Unused metric coverage is omitted too. Needed arithmetic
   dependencies retain anonymous coverage slots. Codes have meaning only within that file;
   they are **not anonymization**. Ranks, numeric results, counts and declared units can still
   identify people or business facts. Publish only information you are authorized to share.
3. Open the Gateway's **Shared analyses** portal (`/analyses`), also linked from Shared
   processes. Connect with a delegated OIDC access token that has explicit analysis scopes.
   The credential stays in memory, is cleared from the input on Connect, and is never stored
   in browser storage. Existing process SSO sessions, service tokens and evidence scopes do
   not provide analysis access. Company SSO for this separate portal is not implemented.
4. Choose the file and review it again; add a meaningful authorized title and retention.
   The browser rejects unexpected fields **before transfer**. The server independently
   validates bounded JSON, refuses duplicate keys and recomputes the snapshot digest.
   Check publication authorization and publish. Initial access is owner plus explicitly
   configured analysis administrators; other people need a per-publication role.
5. Under Access, the owner adds organization actor IDs as viewers or editors. Members need
   both the relevant OIDC scopes and the role. They can inspect, browse retained revisions
   and download an audited JSON export. Editors can publish new revisions from another
   reviewed file; they cannot manage membership, retention or deletion. Historical revisions
   cannot be edited through the portal. Every new publication requires explicit confirmation.

The published snapshot is **publisher supplied**, not independently certified. Its source
hash uses `openworkgraph.discovery-source.v1` (events, stored objects and event-object links;
not temporal object history). The spec hash binds the local normalized analysis definition,
but does not disclose it or let the Gateway replay it. Neither hash is a public signature or
proof of data ownership, completeness, business intent or outcome. Source freshness is always
`unknown_source_not_shared`; local append/delete cannot contact or recall an independently
shared copy. There is no approval or execution-authority propagation.

## Access, revisions and retention

Administrators explicitly map these scopes in `OWG_GATEWAY_OIDC_GROUP_SCOPE_MAP`:

- `org:analysis:read`: prerequisite to read; **does not grant membership**.
- `org:analysis:write`: create private publications; edit only where owner/editor/admin.
- `org:analysis:admin`: administer/read any publication in the current organization, with
  the read/write prerequisites. It gives no access to another tenant or to raw logs.

No analysis scopes are enabled by default. A person with process or evidence permissions
alone cannot read publications. Inventory queries select authorized rows before pagination;
there is no hidden-publication total, hidden-row analysis, arbitrary query or raw case API.
Owner/admin metadata changes and immutable revision creation use row locks / SQLite immediate
transactions and separate compare-and-swap revisions for content and access. Writes recheck
current membership after bounded request parsing. Grant/revoke actions replace the reviewed
complete member list. Conflicts fail instead of partially replacing grants or overwriting data.

The default retention is 7 days, maximum 90; editors cannot renew it by publishing a revision.
Only an owner/admin can renew through Access while the publication is still active. Expired
views are immediately unavailable to reads/exports/updates. Expiry cleanup commits separately
from reads so a later 404 cannot undo deletion. A bounded background worker cleans 100 expired
publications per minute, and organization access also cleans expired rows. A stopped service,
DB outage or backlog can delay physical cleanup; access still fails closed. Startup restarts
cleanup. Shutdown drains an in-flight maintenance operation before closing the database pool.
This does not erase independent downloads or backups governed by separate retention policies.

Revocation applies to future authorized reads/exports, including historical revisions. Already
served responses/downloads cannot be recalled. Changing identity clears tables, uploads and
credentials; late responses cannot restore an older identity's data. Owner deletion removes
all stored revisions and member rows; stale content/access counters refuse deletion. Minimal
security audit entries remain under the Gateway's existing audit retention policy. Audits
contain publication/revision/digest/decision counts, not table bodies, titles, units or filters.

## Bounds and interfaces

`openworkgraph.aggregate-analysis-snapshot.v1` has ≤6 columns (≤2 coded group columns), ≤100
result rows and ≤128 KiB canonical UTF-8 JSON. Numbers must be finite with magnitude ≤2^53−1;
integer values must be exactly representable in the browser. No unsafe numeric value is silently
rounded to fit. Selected full-population counts, result truncation and missing/zero/nonfinite
coverage remain explicit. Per-case arithmetic still happens before aggregation. A truncated
result does not assert all group rows are included. Complete fingerprints require ≤30M source
rows; larger sources fail rather than report a partial fingerprint. There are ≤100 publications
per organization, ≤10 immutable revisions per publication and ≤50 explicitly assigned members.
The history limit refuses new revisions; it never silently prunes an earlier revision.

Local: `POST /v1/event-logs/{id}/analysis-sharing-preview`, normalized analysis spec body,
`X-OWG-Analysis-Sharing-Preview: true`, OWG dashboard session, no-store.

Gateway delegated-human endpoints (`Cache-Control: no-store`):

- `GET|POST /v1/human/analyses`: authorized inventory / new publication.
- `GET|POST /v1/human/analyses/{id}`: exact `?revision=N` / new immutable revision.
- `GET|POST …/access`: owner access metadata / reviewed complete replacement, optional renewal.
- `GET …/export?revision=N`: fresh authorization and atomic audit before JSON export.
- `POST …/delete`: owner confirmation with exact content/access counters.

Server request bodies are ≤160 KiB; extra fields, duplicate keys, nonstandard numbers and invalid
contracts are refused. SQL is parameterized; quotas use an organization lock, and access/expiry
queries have tenant/member/owner/expiry indexes on SQLite and PostgreSQL. Static portal assets
use the existing nonce CSP and package manifest; labels/titles/units are rendered as text.

Acceptance uses owned logs and synthetic delegated identities: actual local browser download
uploaded through the portal; owner/viewer/editor, foreign tenant and service/session refusal;
immutable history, stale writes, revoke/export/delete, retention, malformed/hidden file refusal
before transfer, late identity responses, WCAG/mobile, and SQLite/PostgreSQL transactions.
This is not a real-identity customer pilot. Shared raw datasets, field/case views with server-side
re-query, cancellable jobs, shared signal/pipeline editing and live source freshness remain open M2 work.
