# Saved-log numeric cohort metrics

A local dashboard session may POST `/v1/event-logs/{log_id}/cohort-metric`
with `Content-Type: application/json` and `Authorization: OWG-Session <session>`.
AI bearer tokens cannot use this endpoint. The existing workspace verifies the
saved log and selected case notion, then applies the validated cohort filter.

Example body:

```json
{
  "cohort": {"activity": "Review"},
  "definition": {
    "version": 1,
    "expression": {
      "divide": [{"field": "duration_seconds"}, {"literal": 3600}]
    }
  }
}
```

Allowed numeric fields are `duration_seconds` (case max event timestamp minus
min timestamp), `event_count` (number of events per selected case), and
`resource_count` (distinct nonempty resource strings per case). Existing
derived-metric expression operators and validation apply. The response contains
the definition SHA-256, aggregate count, null count, sum, mean, min and max as
decimal strings, and the validated cohort description; no raw case/event IDs,
source attributes or text are returned. This is descriptive, not a causal
finding or permission to execute actions.

**Safety/limits:** all matched cases must fit within 1,000; larger cohorts
fail rather than silently sampling or reporting misleading partial statistics.
Empty cohorts return null aggregates. The local dashboard session is checked
before request parsing; request body is limited to 8 KiB and duplicate JSON
keys are rejected. The response is no-store. Existing saved-log retention,
deletion and local access rules apply; this is *not* a remote multi-tenant
authorization boundary.

In the dashboard, **Calculate cohort metric** returns the aggregate.
**Review cohort metric cases** calculates the same aggregate and shows its
case values. You can review the highest, lowest, or missing values and open
a case's event timeline. Both actions apply the selected cohort filters and
active follow rules.

Pass `?include_case_values=true` to the ad hoc or
`/cohort-metric-definitions/{metric_id}/evaluate` endpoint to request case
values. The response adds a `case_values` array containing one `case_id`
and a decimal-string or null `value` per matching case. Values are sorted
numerically in ascending order, with missing values last and ties ordered
by case key. The aggregate and the rows come from the same evaluation.
Case values contain no event rows or case attributes. Opening a timeline
reads the current retained case. The same 1,000-case limit and local
dashboard authorization apply to both actions.
