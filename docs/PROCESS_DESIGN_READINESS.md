# Organization design readiness

The shared process workspace provides a paginated readiness inventory over
explicitly shared saved designs. Each entry identifies the immutable revision,
content hash, owner actor, archived state and exact-revision independent review.
Opening an entry loads that exact revision; it does not silently switch to a
newer model after the report was generated.

Findings list IDs for unconfirmed rules, text-only rules, structured predicates
needing a source-fact implementation, unmapped activities, activities/decisions
without declared responsibility, activities with unevaluated manual guards,
and terminals without confirmed source-state expectations. Start, end and
routing nodes do not require an activity implementation suggestion. A declared
human activity is mapped work. Manual guards and absent source expectations
can be intentional design choices.

These are descriptive checks of saved declarations, not a readiness score or
an execution gate. Even a design with no listed findings has not had customer
tool bindings, deployment health, authoritative facts, policy compliance or
business outcomes checked. Independent design review remains separate from
execution authorization. No employees are ranked, and no personal work evidence
or local private knowledge is scanned.

## Scope and pagination

`GET /v1/human/process-readiness` requires a delegated human identity with
`org:process:read`. Tenant isolation matches the shared inventory. Parameters:

- `after`: opaque workflow-ID cursor, at most 80 characters;
- `limit`: 1–100, default 20;
- `owner_actor_id`: optional exact actor ID, at most 512 characters;
- `include_archived`: false by default.

Filtering happens before pagination. Results are ordered by workflow ID and
include `has_more` / `next_after`. Inventory counts and counts of processes
with findings describe only the returned page. They are not organization totals
or counts of unique employees. The next page replaces the report; downloads
contain that current page and its scope/filter metadata. Repeated pages can
reflect later edits: there is no persistent multi-page snapshot token.

Within a page, model, owner, archive and review reads share one database
snapshot on SQLite and PostgreSQL. Later changes require refresh. The owner
filter uses an organization/owner/workflow index; latest-version joins use the
existing knowledge primary key. The API performs no model-provider calls,
source-system reads or business writes. Its minimal audit stores only returned
count and whether another page exists; the report itself is not retained.

## Browser lifecycle

Loading is explicit. Filter changes and Clear readiness report invalidate
pending responses and disable download. Switching identity or disconnecting
also clears results, owner filters and selected archive scope. Credentials stay
in the existing in-memory delegated session. Report text is rendered as data.
Downloading the current readiness page is a separate action that creates an
external copy of already shared design metadata.
