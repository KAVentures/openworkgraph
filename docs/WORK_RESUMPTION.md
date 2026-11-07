# Cross-session and cross-day work resumption

Roadmap #326 A3. `GET /v1/work-resumption` (and `continuity_context.resumption`
in the MCP `get_current_work_context` tool) answers one question: *did the work
in the latest session start in an earlier session or on a previous day?*

## What it reads
- Only rows inside the AI saved-history grant, through the same `lease_window`
  as `/v1/continuity-references`. With access **off**, only the current run is
  visible, so no earlier day is ever compared. With **selected range**, only
  that range plus the current run. Unknown grant modes fail closed.
- Only opaque pointers: `owg:r:` resource references (with provider and kind)
  and `owg:f:` file references. No titles, URLs, paths, hosts, metadata or
  contents leave the module.
- The request needs `X-OpenWorkGraph-Context: ai` and the usual local API auth.

## How it works
1. Activity timestamps inside the grant (newest 200,000 at most) are split into
   sessions at idle gaps (`gap_minutes`, default 30, range 5–720). A session is
   an activity window, not a task and not a user-declared session.
2. Pointers are assigned to sessions. The latest session is the current one.
3. Every earlier session that shares at least one pointer with it becomes a
   candidate with the shared pointers, the pointer overlap (Jaccard), the gap,
   whether it crosses a calendar day (UTC), its evidence event ids and a
   `trace_window` to pass to `get_workflow_trace` as `since`/`until`.

## Confidence
Confidence depends on shared pointers only:

| Evidence | Status | Confidence |
|---|---|---|
| ≥ 2 specific shared pointers and overlap ≥ 0.34 | `likely_resumed` | 0.8 |
| ≥ 2 specific shared pointers | `possible` | 0.65 |
| 1 specific shared pointer | `possible` | 0.5 |
| only routine pointers shared | `ambiguous` | 0.3 |

A pointer is *routine* when it appears in at least half of the scanned
sessions (and at least 3), such as an always-open inbox or dashboard.

- Time alone never creates a candidate: adjacent sessions on different
  resources produce none.
- The gap is reported but never raises or lowers confidence.
- `proves_same_work` is always false, `task_label` is always null and no
  unfinished state is inferred. An empty list does not prove the work is new;
  scans that hit their bounds say so in `coverage.scan_truncated` and `notice`.

## Pointer identity and interleaved cases

A matching opaque token is not sufficient to link two sessions. The reconstruction
engine matches the tuple **(provider, resource kind, opaque reference)**, so
a GitHub pull request and Jira issue with colliding tokens cannot be treated
as resumed work. Different object kinds within one provider remain distinct
even if they have the same opaque reference. File pointers have a separate
local-file namespace. Multiple identities with one opaque token are counted
separately for overlap and routine-context analysis; the legacy
`current_session.resource_refs` list remains a deduplicated display list
for compatibility. This prevents cross-system collisions from inflating
confidence or contaminating an interleaved workflow, without exposing any
additional source text or weakening the saved-history grant.


## Interleaved-case evidence tracks

An idle-gap session can contain several unrelated active business cases. The
existing session-level `evidence_event_ids` and `trace_window` are navigation
context for the **entire** earlier activity window, not proof for any individual
case. Consumers should use each candidate's new `pointer_tracks` when inspecting
a specific resumed object.

Each track corresponds to one exact provider/kind/opaque-reference identity
from `shared_pointers`. It contains `pointer`, plus `earlier` and `current`
with `first_observed_at`, `last_observed_at`, and up to six bounded
`evidence_event_ids` from events actually observing **that identity**.
Tracks are capped at twelve, matching the shared-pointer display cap; the
full `shared_pointer_count` and confidence calculation remain unchanged.
The observations can be separated by other case activity and do **not** imply
continuous work, verified ownership, a completed handoff, or an automatic merge.

This is an additive response field on the existing local authenticated REST
route and MCP context, with no new collection, storage, provider access, grant
or permission. The pointer scan remains bounded and subject to the current AI
saved-history lease. A truncated scan means tracks may omit earlier evidence;
it is never evidence that no other work occurred. The older response fields
remain available for compatible clients.


## Resource-specific continuity timelines (review-only)

The existing `/v1/work-resumption` response now includes `identity_timelines` alongside unchanged `candidates`. For each of up to 12 current-session opaque provider-scoped resources observed in at least two idle-gap sessions, the server returns up to 20 chronological `episodes`: observed first/last instants, a bounded set of event IDs, the containing idle-gap session start, and the elapsed seconds since the previous observed episode. `episodes_truncated` explicitly flags older omitted episodes. Each resource has its own trail, so unrelated cases interleaved within the same session never inherit one another's event IDs. A routine resource is marked `routine_context`; all trails say `proves_same_task: false`. Elapsed time is descriptive and never generates an association or changes confidence.

The same AI saved-history grant and server-side row budgets apply. Access-off and absent pointers yield empty timelines. The new response is advisory navigation, not verified case identity, completion state, authorization, or automatic grouping. The API does not read URLs, titles, work text or clipboard contents. For a user-facing review, inspect individual episode evidence before confirming a workflow. Synthetic SQLite regressions cover interleaved resource trails and grant boundaries; independent held-out F1, contamination and consenting-user acceptance remain unproven.
