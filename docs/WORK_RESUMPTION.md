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
