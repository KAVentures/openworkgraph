# Saved-log numeric cohort metric (R9, integration in progress)

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

**Dashboard integration added (pending CI):** The saved-log analysis view now includes a Cohort metrics section with metric selection (case duration in seconds/hours, events per case, distinct resources per case), the existing case-cohort filters, active follow rules, a read-only calculation action, and aggregate-only output. It refuses oversized cohorts through the API, displays empty cohorts and request errors, and discards results when the selected log/case type or analysis generation changes. A Playwright event-log import/analyse test exercises the authenticated positive path and an empty filtered cohort.

**Not complete:** Exact-head native/browser CI, independent review, safe merge to main, and real consenting-user acceptance remain necessary before R9 is accepted as shipped. Arbitrary expression editing is intentionally not exposed in the UI; the server still validates and supports the documented bounded expression contract.
