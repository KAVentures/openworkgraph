# Event-time object state review (R24)

Open **Event-time object state check** in the object-centric map, or open
**Object binding continuity** from the workspace. Both use the selected
retained OCEL log even if it has no supplied object-to-object assertions.
In the map, select a result status to page matching events and open an event
to review its bounded object witnesses. Changing the check clears earlier
results; a changed log requires rerunning the check.

Enter an exact activity, object type, attribute name and expected retained
string (including an empty string if appropriate). Select equality or
inequality, and all or any linked objects. Optionally restrict the event-object
qualifier to the exact literal value, including the empty qualifier. Every
recorded event of the selected activity is included, including events with
no selected object. Duplicate qualifier links to the same object count once.

## Time and uncertainty

The latest retained attribute timestamp at or before the event applies.
A value recorded at the event timestamp is included. The OCEL initial
timestamp 1970-01-01 is treated as the stored initial convention. No
first-listed current-value fallback is used. Missing history, a timestamp
before the first recorded value, and conflicting values at the latest
timestamp are separately reported as unknown. A later unambiguous value
resolves a previous ambiguity for later events. No numeric or boolean
coercion is performed: comparisons use the retained string representation.

For all objects, any known mismatch makes the event not matched; otherwise
unknown dominates a match. For any object, a known match makes the event
matched; otherwise unknown dominates a mismatch. Both modes report
no_linked_object separately instead of vacuous success.

The result contains exact event and distinct event-object counts over the
retained selection, and a status-filterable evidence table. Each event
includes all object outcome counts and up to 20 literal object witnesses,
with an explicit truncation flag. Pagination is chronological with event
ID as deterministic tie-breaker, not an inferred temporal order. Later pages
require the retained append revision; append invalidates an older revision.
Rerun after changing filters. The report does not establish historical
source completeness, typed policy semantics, actual policy compliance,
causal results or execution rights.

## API

POST /v1/event-logs/{log_id}/object-state-check

Local dashboard session required; no-store response. Strict JSON, 16 KiB.
Example:

```json
{"activity":"Pay","object_type":"order","attribute":"status",
 "expected_value":"approved","operator":"eq","quantifier":"all",
 "event_qualifier":"","status":"unknown","limit":30}
```

Omit event_qualifier to use every qualifier. Omit status to page all events.
Use next_offset and revision as offset and expected_revision on a later page.
Limits: 200,000 selected events, 1,000,000 distinct selected event-object
links, 2,000,000 selected history rows, 1–100 events per page.
An exceeded work bound refuses the analysis instead of presenting sampled
counts as exact. No new persistent state, model change or provider calls.

The test fixture covers exact-time updates, conflicts and later resolution,
missing attributes and objects, duplicate qualifier associations, all/any
semantics, pagination and authentication. Real customer acceptance and
large-scale latency are not established by the fixture.
