# Inspect supporting cases in large event logs

The local event-log workspace connects its variant and activity tables to the
complete supporting case histories. Import remains an explicit local-storage
action. Inspection reads that retained log; it does not copy business logs into
canonical desktop evidence or grant connected AI apps access.

Choose **Inspect cases** beside a variant to select that exact full path, or
beside an activity to select cases containing it. The case-evidence panel also
supports the existing period, resource, activity and case-attribute cohort
filters. These are whole-case selections: the timeline retains steps performed
by other resources and steps outside the selected activity. A variant and a
cohort are intersected when both are selected. **All paths** removes the variant
condition; **Browse cases** applies the visible cohort fields.

Case pages report the full matching count and show 30 cases at a time. Open a
supplied case key to inspect activities, UTC timestamps, resource labels and
source event IDs. **More case events** loads the next 100 events, until every
event is available. The display states how many have been loaded and how many
remain. Large histories are paginated rather than silently clipped.

Keys and labels preserve literal whitespace, case, Unicode and separators.
OCEL queries are restricted to the selected object type; an event with several
qualified relationships to one object appears once in that object's history.
Events involving different objects can appear in each object's case. A log
with no declared objects has no available case notion; OWG reports that instead
of inventing a case identifier.

Events are sorted by timestamp, then event ID and literal event fields for
stable display. Equal-timestamp events are counted and explicitly identified:
this display order does not establish their business order. Elapsed time is
first-to-last logged event time, not employee effort or verified waiting time.
Supplied resource labels do not establish employee identity or handoffs.

Changing the selected log or object type, reanalysing, and deleting a log
invalidate pending case results. A delayed response cannot repaint evidence
from an older selection. Inspection does not approve a process, verify source
completeness or business outcomes, or authorize execution.

## Dashboard API

Both routes require the local dashboard session and JSON content type. AI
bearer access is refused. Responses are `Cache-Control: no-store` and retain the
workspace's descriptive-only fields. No query or response is retained elsewhere.

`POST /v1/event-logs/{log_id}/cases/query?case_type=order`:

```json
{"cohort":{"resource":"bot","activity":"Review"},
 "path":["Receive","Review","Close"],"limit":30}
```

All selectors are optional. `cohort` uses the existing cohort definition;
`path` accepts 1–500 literal activity names in exact order. The response gives
`selected_cases`, a bounded `cases` page and `next_after`. Supply the returned
`next_after` as `after` with the same selectors to read the next page; the case
page maximum is 100. Timestamp ties and event counts describe each whole case.

`transition: ["Review", "Close"]` selects cases containing that exact adjacent
pair in timestamp/event-ID display order. It intersects with the cohort and
exact path when supplied; merely containing both activities does not match.
Repeated pairs count once for case selection, and the complete case remains
available. Equal timestamps do not verify the displayed business order.

The object-centric map opens this same case browser from a keyboard-accessible
activity or edge. Map inspection keeps its own explicit object type, even when
the main analysis uses another type. The supporting-case browser can further
restrict that selection by a whole-case cohort. Counts in the map cover the
complete log for the selected object type; the map discloses displayed versus
total activity and transition types. Its combined routes are not inferred case
histories, causal dependencies, approved rules or verified human handoffs.

`POST /v1/event-logs/{log_id}/case-events/query?case_type=order`:

```json
{"case_id":"literal supplied key","offset":0,"limit":100}
```

The response includes `total_events`, `timestamp_ties`, the current `events`
page and `next_offset` (null when complete). The maximum event page is 250.
Case lookup never crosses the selected log or object type. Deletion removes
the underlying log; subsequent inspection fails safely. Exact-path filtering
uses a deterministic timestamp/event-ID ordering, including when timestamps
tie, and should not be interpreted as verified conformance to business policy.
