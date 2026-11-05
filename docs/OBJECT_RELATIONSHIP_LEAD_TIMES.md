# Cross-object lead times over supplied relationships (R24)

OpenWorkGraph can now measure **observed elapsed time between selected activities on two explicitly related OCEL objects**. For example, how long it was between an order's first `Approve` event and the first `Ship` event on each delivery to which that order is linked in the imported OCEL 2.0 object-to-object graph.

This is a task/process-mining competitor-parity feature, not a claim that the relationship is causally correct or that imported events are complete. It is particularly useful when flattening an order, item, delivery, and invoice process onto one case would lose cross-object information.

## Local interactive explorer

The full-app entry point is **Evidence → Process mining → Explore OCEL object relationship lead times**.
It opens `/object-relationship-lead-times` in the same dashboard tab, preserving the existing
local dashboard session. The standalone page discovers only already retained local OCEL logs,
shows the supplied directed object-relationship groups and their counts, and lets a human choose
the source/target object types, exact activity names, optional qualifier filter and number
of detailed examples. It retrieves activity suggestions from the two separately chosen
object-case notions and submits directly to the existing session-only analysis API.

The result shows exact forward/reversed/missing pair counts, repeated activity counts, forward
p50/p90, bounded object-ID examples, and stored graph revision/digest. It expressly refuses
to call these elapsed times verified handoffs or causal delays. Results are never persisted
in a browser cache or sent to a cloud service. Selection changes invalidate old results;
an in-flight response cannot overwrite a newer selection.

The HTML page and JS themselves contain no customer data and may load without a session.
**Any attempt to read or analyze imported records still requires an active OWG dashboard
session; bearer-only AI tokens cannot access this API.** The page uses a restrictive
Content Security Policy (`script-src 'self'`, `connect-src 'self'`, no cross-origin
resources), text-only DOM writes and no tracking code. It shows a launcher-reopen instruction
if no current session is available. Opening a separate new tab rather than the same tab
may not have the dashboard's sessionStorage grant.

The real Chromium accessibility journey is
`NODE_PATH=<playwright-and-axe-dependencies> node tests/browser/object_relationship_lead_times.cjs`
and is part of the `explorer-accessibility` GitHub workflow. It tests an actual local
OCEL import, complete in-browser selection and analysis, aggregate-only results,
empty qualifier handling, anonymous session refusal, WCAG 2.1 AA and 390-pixel layout.
It has no provider calls, source writes or synthetic claims of customer acceptance.

## Local authenticated API

`POST /v1/event-logs/{log_id}/object-relationships/lead-times`

This endpoint is **dashboard-session only**, like other locally retained event-log workspace requests. It rejects bearer-only AI tokens and runs no source/provider call. The event log must already have been imported or retained by an expressly authorized user. Request body is strict JSON (16 KiB maximum, no repeated keys or nonfinite values).

```json
{
  "source_type": "order",
  "target_type": "delivery",
  "source_activity": "Approve",
  "target_activity": "Ship",
  "qualifier": "fulfills",
  "sample_limit": 20
}
```

The `qualifier` filter is optional. An explicit empty-string qualifier matches only empty qualifiers. When no qualifier is requested, multiple qualifiers on the same directed source/target pair **do not double-count that pair**. This is a different concept from shared-event associations: only supplied directed object-to-object assertions qualify; OWG never guesses a relationship from co-occurrence.

### What the result means

A sample response has these fields:

- `pairs`: number of distinct selected directed object pairs.
- `forward_pairs`: both selected activities were observed, and the target object's first event is at or after the source object's first event.
- `reversed_pairs`: the target object's first selected event precedes the source object's first selected event. These are separately counted, **not** treated as negative waiting times or silently discarded.
- `source_activity_missing_pairs`, `target_activity_missing_pairs`, `both_activities_missing_pairs`: explicit absence in the imported record. This is **not proof of a business step being skipped**.
- `pairs_with_repeated_selected_activity`: at least one selected activity repeats on the source or target; timing uses only its **first** occurrence. Repeat-event ambiguity is visible.
- `same_timestamp_pairs`: an apparent zero-second delay; not proof that any handoff happened.
- `forward_lead_time_seconds`: minimum, p50, p90, maximum and mean **only among forward pairs**; all are `null` when no forward timing can be calculated.
- `samples`: bounded exact object-ID examples and observed first timestamps, with reversed/missing pairs listed before ordinary forward examples. `sample_truncated` says whether some pair examples were omitted. Exact analytics still cover the full selected pair set.
- `relationship_revision`, `relationship_digest` and `object_index_digest`: provenance of the locally retained relationship/object graph. Results refer to one read of the saved log.
- `execution_authorized: false`, `causal_claims: false`, `source_data_completeness_verified: false`.

A source and a target may legitimately have events at the same timestamp; this does **not** establish a physical transfer or real business causality. Similarly, first-to-first timing is not necessarily time spent actively working or a measured staffing requirement.

## Work and privacy bounds

The analysis is exact for at most **20,000 distinct selected relationship pairs** and **1,000,000 selected linked event occurrences**. Exceeding either bound returns an explicit 422 error; it never silently truncates aggregate statistics. Sample examples are separately capped at 50 (default 20; set 0 for aggregate-only use). Unknown object types/activities or no qualifying relationship produce zero selected pairs and null duration statistics, not fabricated timings.

The requested types/activities and selected sample object IDs are visible only to a currently authenticated **local dashboard session**. HTTP responses use `Cache-Control: no-store`. This feature does not enable new desktop/browser capture, work-text extraction, employer access, MCP export or cloud upload, and it does not change consent settings. Stored logs are removed by the existing log-delete mechanism; later analysis of a deleted log is refused.

## Limits and validation

- Imported O2O relationships are **caller supplied and static**. No observed effective dates, validity, business authority or completeness can be inferred from them.
- Ordering is based on the imported UTC event timestamps and first matching event on each selected object. The same event may be linked under multiple event-object qualifiers; it is counted once per object.
- No time-based case linking, causal inference, process conformance, intervention benefit, authorization or employee performance assessment is implied.
- Independent user review and real-company acceptance are still necessary for competitor parity.

Implementation: `server/event_log_object_lead_times.py`, the separate session-only `server/event_log_object_lead_time_routes.py`, and registration in `server/secure_app.py`. Tests exercise fixture-based exact metrics, literal qualifiers, repeat-event deduplication, reversed/missing cases, bounded refusal, private deletion and a real local HTTP authentication flow. No conflicting dashboard file was modified. A graphical dashboard panel can be layered on when its current owner releases it.
