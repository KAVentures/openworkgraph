# R9: Linked business-attribute case analysis (local API)

This is a functional, additive **read-only** analysis for an already imported OCEL 2.0
event log. It answers a common analyst question: *How many observed cases fall
under a business attribute stored on a related object, and what is their
observed median elapsed duration?*

For example, an `order` case may be linked through a co-observed event to an
`invoice` object carrying a `region` attribute. A case with exactly one
such invoice and one unambiguous stored invoice definition can be counted under
that region. No inferred temporal joins or external provider reads are made.

## Dashboard: Analyze linked business attributes

After explicitly importing a local **OCEL 2.0** log with at least two object types,
open **Evidence → Large event logs**, select the stored log and the desired
case notion, and choose **Analyse**. Under **Build an analysis** is a separate
**Analyze linked business attributes** section. Choose the related object type,
enter a supplied attribute name (for example `region`), choose 1–100 groups,
then click **Analyze linked attributes** or activate it with Enter.

The dashboard makes no joined-analysis request until this explicit action. It
uses the existing authenticated local session and current log/case notion,
and renders the exact API resolution counts: resolved, unlinked, ambiguous,
missing attribute and oversized attribute. The table shows only resolved
supplied values, observed case counts and median **elapsed** seconds. It also
shows the number of omitted groups/cases and the examined event/link budgets.
Changing the log, case notion or inputs clears stale results and disables
exports; responses for superseded selections cannot reappear.

**Download linked analysis JSON** explicitly exports the full returned
counts-only response. **Download linked analysis CSV** explicitly exports the
visible grouped rows; spreadsheet-formula-leading source values are prefixed
to prevent CSV formula execution. No automatic download, provider read,
cross-log join, raw case/object identifiers, AI handoff or business action
occurs. The dashboard does not yet expose the API's optional cohort filters,
saved-metric expressions or time-varying as-of joins. Use the REST API below
for typed cohort selection; do not present this UI as general PQL.

A native Chromium acceptance is registered in `.github/workflows/tests.yml`
and covers actual OCEL import, keyboard-triggered session-authenticated
analysis, unresolved/multi-linked cases, local input refusal, JSON/CSV
downloads including formula safety and case-change invalidation. The
backend's separate tests cover authorization, tenant/log isolation and
ambiguous object definitions. Neither constitutes real-customer acceptance.

## Authenticated REST request

```http
POST /v1/event-logs/{log_id}/linked-attribute-analysis
Authorization: OWG-Session <current-local-dashboard-session>
Content-Type: application/json

{
  "case_type": "order",
  "related_object_type": "invoice",
  "attribute": "region",
  "cohort": {},
  "limit": 50
}
```

The endpoint requires the same live local dashboard session as existing
event-log analyses. Bearer AI tokens cannot access it. It accepts a strict
JSON object (up to 8 KiB), refuses duplicate keys, and does not persist
analyses or make business changes. All supplied strings enter SQL as bound
parameters, never SQL fragments.

The response contains `format:
openworkgraph.event-log-linked-attribute-analysis.v1`, the selected log
metadata and case notion, total cases, counts for each resolution status,
and up to 100 sorted `rows` containing `value`, `cases` and
`median_duration_seconds`. It also includes group totals, number of
groups and cases omitted by the result limit, cohort metadata and exact
work-budget ceilings. The response is `Cache-Control: no-store`.
No raw case or related-object identifiers are returned.

## Semantics and safety

- The selected case notion and related object type must differ. The related
  type must actually exist **in the selected log**. No cross-log or external
  object joins are allowed.
- Each selected case's events are examined for objects of the requested type
  co-observed on the **same event**. Repeated event-object links are deduped.
- **resolved**: exactly one distinct related object, exactly one stored object
  definition and a nonempty attribute string no longer than 120 characters.
  Only resolved cases contribute to the grouped breakdown.
- **unlinked**: no related object observed; **ambiguous**: more than one
  related object or contradictory stored definitions; **missing_attribute**:
  the uniquely linked object's attribute is absent or empty;
  **oversize_attribute**: its value exceeds the disclosed 120-character cap.
  Ambiguous and missing values never become zero, an arbitrary representative
  value, or an inferred business relationship.
- `cohort` uses the existing case-cohort grammar and filters full cases
  before joining, calculating durations or denominators. Duration is elapsed
  time between the first and last *observed* case events, not verified work
  time or a business outcome.
- A request is refused when selected case-event rows exceed 2,000,000 or
  the selected event-object links exceed 4,000,000. The response reports the
  examined counts. Groups are ordered by case frequency, then value, and
  truncation is explicit.
- Values are supplied object attributes, **not verified source-of-truth
  business fields**. This does not implement general PQL/SQL, arbitrary
  join predicates, time-varying as-of joins, saved-metric integration,
  dashboard authoring, cross-tenant analytics or customer validation.

## Tests and acceptance

`tests/test_event_log_linked_attribute_analysis.py` exercises a real
OCEL import with multiple cases and related objects, duplicate event links,
two co-observed objects with identical labels (still ambiguous), absent and
oversized attributes, conflicting stored object definitions, a case cohort,
group truncation, isolation between two log IDs, invalid requests, work-budget
refusal and session-authenticated REST with no-store/strict JSON semantics.

These tests must execute in native CI alongside the repository's required
Ubuntu/macOS/Windows pytest, browser, collaboration, scale, accessibility,
starter and packaging workflows. Passing synthetic fixtures alone does not
establish provider/customer acceptance. No automatic agent execution is
authorized by this analysis.
