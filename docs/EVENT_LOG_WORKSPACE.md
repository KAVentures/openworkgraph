# Event-log workspace (CSV, XES, OCEL 2.0)

The event-log workspace imports business event logs that you export from your
own systems and analyses them on this computer with an embedded columnar
engine (DuckDB). It is meant for logs that are too large for the in-browser CSV
miner, which stays limited to 1 MB / 5,000 rows and keeps nothing.

Nothing is uploaded anywhere. Imported logs are written to
`<data dir>/event_logs.duckdb` (owner-only permissions) and stay there until you
delete them.

## Formats

| Format | Import | Export | Notes |
| --- | --- | --- | --- |
| CSV | yes | yes | You map the case, activity, timestamp and (optional) resource columns. |
| XES (IEEE 1849) | yes | yes | `concept:name`, `time:timestamp` and `org:resource`; other string-like attributes are kept (32 max, 500 chars each). |
| OCEL 2.0 JSON | yes | yes | Objects, object types, events and event-to-object relationships with qualifiers. |
| OCEL 2.0 XML | yes | yes | The same content in the OCEL 2.0 XML layout (`.xmlocel`). Objects must precede events, as the standard requires; the file is streamed, so memory stays bounded. |

Every log is stored object-centrically. A flat CSV or XES log becomes one
object type, `case`. For an OCEL log you choose the case notion (`order`,
`item`, ...) per question, and the overview reports which object types share
events.

Timestamps must carry a timezone (`Z` or an offset). Rows without one, or with
an unparseable time, are counted as rejected and never guessed. XML with a
`DOCTYPE` or entity declaration is refused before parsing.

## Object-centric maps

For an OCEL log, **Object-centric map** draws one directly-follows map per
object type: every object (an order, an item, a delivery) is one lifecycle,
so an order's map never mixes in the steps of its items. Each type also
shows how many objects and events it has, the median events per object, and
how lifecycles start and end. Links between types count objects that share
at least one event (for example, "each order is linked to 2 items on
average, at most 3"), and the map lists activities that touch several object
types, which is where lifecycles converge or diverge. Links come from shared
events only; they are not verified relationships. Maps are capped at 64
activities and 400 transitions per type, and 20 object types.

The map counts observed object lifecycles (objects with at least one linked
event). Inter-type links cover the displayed object types. The share uses all
observed source-type objects; the mean uses only source objects with at least
one link to that destination type. Counts deduplicate repeated shared events
and multiple qualifiers relating the same event to an object.

Batch events can link thousands of objects at once. The query groups identical
complete event-ID sets to reduce repeated arithmetic while retaining exact
weighted object-link counts. Each object keeps its individual case history.
The overview also reduces shared-event counts to distinct event/type pairs.

Relation analysis bounds pair work to two million candidate pairs; event-set
grouping uses at most one million input links. If neither representation fits,
or the relation calculation reaches its memory limit, link counts are unknown.
Lifecycle maps remain available. API clients must inspect
`relations_calculation_complete`; an empty `relations` list with a false flag
does not establish absence of links. Responses include a fixed unknown reason,
count method, scope and work limits. No partial link estimates are reported.

## Limits

- 4 GB per import, 50 stored logs (delete one to import another).
- Identifiers, activity names and resource labels are bounded to 200 Unicode
  characters; qualifiers to 80. Oversized values, non-string identifiers and
  control characters are rejected instead of truncated or cleaned into another
  key. Nonblank identifiers and labels preserve case, Unicode spelling and
  surrounding whitespace exactly.
- Acceptance: a 10M-event, 1M-case CSV imports in about a minute and every
  analysis query returns in well under 30 seconds on a CI runner
  (`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_scale_acceptance.py`).

## API

All routes need the local dashboard session (`Authorization: OWG-Session ...`).
AI bearer tokens and MCP clients cannot read or change imported business logs.

| Route | Purpose |
| --- | --- |
| `GET /v1/event-logs` | List stored logs. |
| `POST /v1/event-logs/import?format=csv\|xes\|ocel\|ocelxml&name=...` | Import the request body. Requires `X-OWG-Local-Storage-Confirmed: true`. CSV also takes `case_column`, `activity_column`, `timestamp_column`, `resource_column`, `delimiter` (`t` for tab) and repeated `attribute_column` for up to 32 extra columns to keep as event attributes (nothing else is stored). |
| `POST /v1/event-logs/{log_id}/delete` | Delete the log and every row that belongs to it. |
| `GET /v1/event-logs/{log_id}/summary` | Events, cases, activities, time span and case-duration median/p90/mean. |
| `GET /v1/event-logs/{log_id}/object-types` | Object types, objects per type and shared events between types. |
| `GET /v1/event-logs/{log_id}/object-map` | Per-object-type lifecycle maps, links between object types and activities shared by several types. |
| `GET /v1/event-logs/{log_id}/variants` | Most frequent variants with share and median duration. |
| `GET /v1/event-logs/{log_id}/directly-follows` | Directly-follows graph with start and end activities. |
| `GET /v1/event-logs/{log_id}/activities` | Activity frequency and rework (cases that repeat the activity). |
| `GET /v1/event-logs/{log_id}/kpis?sla_seconds=&automation_resources=` | Throughput p50/p90/p95, rework rate, SLA breach rate, automation rate and weekly completions. |
| `POST /v1/event-logs/{log_id}/benchmark` | Compare two case cohorts (JSON body, below). |
| `POST /v1/event-logs/{log_id}/map-comparison` | Compare the process maps of two cohorts path by path (below). |
| `GET /v1/event-logs/{log_id}/simulation/calibration` | Arrival rate, routing, activity times and resources measured from the log. |
| `POST /v1/event-logs/{log_id}/simulate` | Run a what-if scenario against the measured baseline (JSON body, below). |
| `POST /v1/event-logs/{log_id}/conformance` | Align observed variants with a reference model (JSON body, below). |
| `GET /v1/event-logs/{log_id}/model-discovery?noise=&activities=` | Discover a draft process model with decisions, loops and parallel steps for review in the process catalog (below). |
| `GET /v1/event-logs/{log_id}/waiting-time?lifecycle_attribute=&end_attribute=` | Processing and waiting time per activity from lifecycle or end-time attributes (below). |
| `GET /v1/event-logs/{log_id}/root-cause?outcome=...` | Case features associated with an outcome, with confidence intervals. |
| `GET /v1/event-logs/{log_id}/export?format=csv\|xes\|ocel\|ocelxml` | Download the log. |

Analysis routes take an optional `case_type` for OCEL logs. Every response is
`Cache-Control: no-store` and is marked descriptive: it reports what the log
contains and makes no causal claims, verifies no business policy and
authorizes nothing.

## Analytics

The dashboard card **Large event logs** (Evidence tab) offers all of the
following after **Analyse**.

### KPIs

- **Throughput**: time from a case's first to its last event (p50, p90, p95,
  mean), plus completions per week.
- **Rework rate**: share of cases that repeat at least one activity.
- **SLA breach rate**: share of cases whose throughput exceeds the SLA you enter.
- **Automation rate**: share of events performed by the resources you list
  (for example `bot, system`). OWG does not guess which resources are automated.

KPIs can be limited to a cohort with the query parameters `started_from` and
`started_before` (timezone-qualified ISO times; a case belongs to the period
its first event falls in), `resource` (a resource took part), `activity` (the
case contains it) and `attribute` (`name=value` on the case object). The
dashboard downloads the result as JSON or CSV; CSV cells that could be read as
spreadsheet formulas are prefixed with `'`.

### Cohort benchmark

```json
{"cohorts": [{"label": "Q1", "started_from": "2024-01-01T00:00:00Z", "started_before": "2024-04-01T00:00:00Z"},
             {"label": "Q2", "started_from": "2024-04-01T00:00:00Z", "started_before": "2024-07-01T00:00:00Z"}],
 "sla_seconds": 172800}
```

Each cohort uses the same filters as KPIs. The response gives, per cohort,
cases, median and 90th-percentile throughput, rework rate and SLA breach rate;
the second-minus-first differences in rework and SLA breach rate (Newcombe 95%
interval) and in median throughput (percentile bootstrap over up to 5,000
sampled cases per cohort, 400 rounds, fixed seed); and how often each activity
and variant occurs in each cohort. Cases in both cohorts are counted, and the
note warns that intervals then overstate certainty. Differences describe the
cohorts; they do not say why the cohorts differ.

### Conformance

The reference model is one or more accepted paths:

```json
{"format": "openworkgraph.reference-model.v1",
 "paths": [["Receive", "Review", "Approve", "Close"]]}
```

The most frequent variants (500 by default, `max_variants` up to 2,000; the
response reports the share of cases they cover) are each optimally aligned with the closest path, at a cost of one per *skipped*
reference step and one per *extra* observed step. Fitness per case is
`1 - cost / (trace length + shortest path length)`. The case-weighted mean
is reported together with the share of perfectly fitting cases, the steps
most often skipped or added, and the most common deviating variants. Up to
10 paths of up to 100 steps are accepted. The reference is whatever you
enter; it is not treated as approved policy.

### Root cause

Choose an outcome: case takes longer than N hours, case has rework, case
contains an activity, or case ends with an activity. Every case feature
(activity present, resource involved, case-object attribute value) that both
5+ cases have and 5+ cases lack is compared:

- outcome rate with and without the feature, and the risk difference;
- a 95% Newcombe hybrid-score interval for that difference;
- whether the interval still excludes zero after a Bonferroni correction for
  the number of features tested.

These are associations in the imported log. They are not causes: a feature
can be a consequence of the outcome (a fix step in slow cases, for example),
and temporal order is not checked.

### What-if simulation

A discrete-event simulation of cases flowing through activities. Each
activity has a pool of interchangeable resources that serve one case at a
time, first come, first served. **Load measured values** fills in, from the
log:

- arrivals per hour (selected complete cases started, over their span of case starts);
- routing: the most frequent variants with their shares;
- minutes per activity: the median gap before the activity, which includes
  any waiting, so the baseline can overstate processing time;
- resources per activity: the distinct resources recorded for it (1 if none).

Change any of them, add activities the log does not have, and run:

```json
{"format": "openworkgraph.simulation-scenario.v1", "label": "Validate first",
 "arrivals_per_hour": 2.5,
 "paths": [{"activities": ["Receive", "Validate", "Review", "Close"], "weight": 0.8},
           {"activities": ["Receive", "Review", "Fix", "Review", "Close"], "weight": 0.2}],
 "activities": {"Validate": {"minutes": 5, "resources": 1}, "Review": {"resources": 3}},
 "distribution": "exponential", "cases": 2000, "seed": 1}
```

Every field is optional; omitted ones keep the measured value. The measured
baseline and the scenario are simulated with the same seed, and arrivals and
routing come from the same random streams in both runs. The response gives
median, 90th-percentile and mean cycle time, completions per day,
utilization, offered load and waiting per activity, and flags activities
whose offered load is 1 or more (the queue grows without bound). The first
10% of cases are a warm-up and are not measured. At most 20,000 cases, 20
paths and 100 activities.

Calibration selects complete paths of at most 100 events. It first counts case
lengths, excludes longer cases whole, then keeps cases whose activities fit
the 100 most frequent activities among the remaining complete cases. The most
frequent paths (10 by default) form the calibration population. No path is
shortened to fit a limit. Counts disclose exclusions by path length, activity
limit and variant limit; these disjoint counts plus calibrated cases equal all
observed cases. Routing weights, arrivals, resource counts and median gaps all
use this same selected population. They do not describe omitted cases.
Complete means all supplied events for a case key in this import; it does not
verify that the source captured an entire business case or its completion.

If fewer than two selected cases have distinct start times, enter an arrival
rate explicitly; both runs use that rate as a disclosed user assumption while
the measured rate remains unknown. With no supported complete paths, the dashboard reports the
coverage and no baseline simulation can run. Responses include the population
counts and limits alongside the calibration, including in what-if results.

The engine is checked against queueing theory in the test suite (M/M/1,
M/D/1 and M/M/2 waiting times within 8%). It models no calendars, breaks,
batching, priorities or abandonment. Results are model outputs under the
stated assumptions, not forecasts or measured outcomes, and authorize
nothing.

## Literal case identity and qualified relationships

A case key is supplied data, not a guess about business intent. CSV grouping
uses the exact selected field. Repeated rows with that same literal case key
remain one declared case; different keys never merge because they share a
prefix or differ only in surrounding whitespace or Unicode spelling.

XES traces and events, and OCEL objects and events, require unambiguous unique
identifiers within the imported log. XES's generated identifiers for omitted
names/IDs must not collide with explicitly supplied values. Duplicate or
conflicting IDs reject the complete import; its transaction leaves no partial
log. The columnar loader checks staged uniqueness without retaining every XES
event ID in a Python set. Missing fields and invalid timestamps retain their
existing rejected-event accounting.

An OCEL event may have multiple qualified relationships to the same object.
Their original qualifiers are preserved in storage and export, but the event
appears once in that object's case history. Two distinct events with identical
activity/time stay separate. An event linked to two different objects can still
appear in both cases; projection event counts are counts within the selected
case notion, not necessarily the raw log's global event count. Unknown foreign
object references do not become associations to a normalized prefix key.

These checks apply to new imports. Previously truncated/normalized identifiers
cannot be recovered from stored data; reimport the original export to obtain
literal identities. Existing logs are not rewritten. None of these mechanical
checks verifies provider/account identity, authoritative business boundaries,
source completeness, people, business outcomes or permission to execute.

CSV headers are bounded to one million characters and must have nonempty,
unambiguous names, including casing. Automatic column renaming is refused.
OCEL JSON rejects repeated fields and nonstandard numeric constants instead
of silently taking one of two conflicting identifiers.

The KPI API accepts repeated `automation_resource` query parameters for literal
resource labels, including commas and surrounding spaces. The older
`automation_resources` comma-separated shorthand still trims each item.
The dashboard accepts a JSON string array for exact resource labels and
reference-model JSON for exact activity labels; its suggested reference is
JSON so separators and whitespace cannot change imported activity names.
CSV column selection also preserves literal header spelling.


## Supporting case histories

Variant and activity tables open complete, paginated case histories with
whole-case cohort filtering and explicit timestamp-order uncertainty. See
[case evidence](EVENT_LOG_CASE_EVIDENCE.md) for interaction and API details.


## Saved process graph comparison

Load an exact local catalog revision, inspect its map and compare selected
retained cases using the shared structural engine. Full-population results keep
bounded unknown cases separate from matches and deviations. See
[saved process conformance](LARGE_LOG_SAVED_PROCESS_CONFORMANCE.md).

Simulation routing accepts weighted text lines or JSON arrays of
`{"activities":["literal label"],"weight":0.5}`. Calibration uses JSON when
text formatting would change a label or routing probability. Small observed
weights retain their precision; a zero-weight route may be disabled when
another route has positive weight. Activity labels are data keys, including
names that also occur on JavaScript object prototypes.

## Organizational mining (handovers)

`GET /v1/event-logs/{log_id}/organization` (dashboard session only) describes how work moves
between the resources named in the log's resource column:

- `handover_of_work`: consecutive events in a case performed by different resources, with
  handover and case counts;
- `working_together`: pairs of resources that both touched the same cases;
- `activity_resource_matrix`: event counts per activity and resource;
- `resources`: events, cases and distinct activities per resource. Resources listed in
  `automation_resource` are marked `automation`.

Resources are pseudonymous by default (`Resource 01`, numbered by event count within that
response). Pass `names=shown` to see the names from your own log. Resources beyond the 200
most active are grouped as `Other resources`. Cases touched by more than 50 resources are
left out of the shared-case pairs and counted in `working_together_excluded_cases`. The
same case filters as KPIs apply (`started_from`, `started_before`, `resource`, `activity`,
`attribute`).

No per-person duration, waiting time, speed, ranking or performance score is calculated,
not even on handover edges. Handover counts say who touched a case next, not who was
responsible or how well anyone worked. One million events are analysed in seconds
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_organization.py`).
## Signals

Signals are saved conditions you check a log against. This is local, signal-only parity with
Celonis Action Engine and UiPath process alerts. Rules belong to one log, are immutable
(delete and add again to change one), are limited to 50 per log, and are deleted with the log.

- `GET /v1/event-logs/{log_id}/signals` lists the rules.
- `POST /v1/event-logs/{log_id}/signals` adds one, for example:
  `{"name":"Left open","when":{"kind":"cases","condition":"open_longer_than","seconds":172800,"end_activities":["Close"]}}`
- `POST /v1/event-logs/{log_id}/signals/{rule_id}/delete` removes one.
- `POST /v1/event-logs/{log_id}/signals/evaluate` checks every rule now.

Rule kinds:

- `kpi`: `case_count`, `median_throughput_seconds`, `p90_throughput_seconds`, `rework_rate` or
  `sla_breach_rate` (with `sla_seconds`), compared `above` or `below` a threshold. Rates are
  fractions.
- `cases`: `duration_over` (seconds), `contains_activity` / `lacks_activity` (activity),
  `has_rework`, or `open_longer_than` (seconds plus the activities that end a case, measured
  against the log's latest event, not the wall clock). `minimum_cases` sets how many matches
  trigger the signal.

`open_longer_than` measures age since the **first retained event**, including
cases with recent activity. It does not measure idle time since the last event.
A case matches only if its last reported activity is outside the selected ending
activities; tied timestamps follow retained event-ID order, as in the timeline.
First/last-activity analyses use that same order.
This describes supplied log order and the retained observation window, not an
independently verified business opening, closure or reopening time. Evidence
shows the first/last event, age since the first event and idle time separately.

Each rule can also carry the KPI case filters (`cohort`) and a `case_type`. Checking returns
whether each signal holds, its value or matching-case count, up to 50 evidence cases, and what
changed since the previous check. KPI rules report whether they flipped. Case rules report how
many matching cases are new. Up to 10,000 matching IDs per rule are remembered,
alongside the exact matching-case count. When either comparison population is
partially retained, `new_cases_since_last_evaluation` stays null unless an exact
count follows from the available evidence. `new_cases_comparison` returns lower
and upper bounds, population/retained counts and a reason; the screen shows the
range as bounded history. Cases moving into a retained sample are not presumed
new. A first check has no prior comparison. Older stored states lack the prior
population count and remain explicitly unknown until a subsequent complete
comparison is possible. Triggered status and total matching cases still use
the full selected population, independently of the history cap.

A signal never sends messages, calls source systems or runs automations. It describes the
saved log; it is not a verified business problem, a cause or an instruction to act.

## Ad-hoc analyses

`POST /v1/event-logs/{log_id}/analysis` runs a typed analysis: a bounded, no-SQL counterpart
to Celonis PQL-style custom analyses. You choose the parts from fixed lists; nothing you type
is executed as a query. Attribute names and values are passed as parameters.

```json
{"level": "cases", "dimensions": ["attribute:region", "start_month"],
 "measures": [{"fn": "count"}, {"fn": "median", "of": "duration_seconds"}],
 "where": {"ends_with": "Close", "duration_over_seconds": 3600},
 "cohort": {"activity": "Approve"}, "order": "first_measure_desc", "limit": 100}
```

- `level`: `cases` or `events`.
- Case dimensions: `start_month`, `start_week`, `end_week`, `first_activity`, `last_activity`,
  `variant`, `has_rework`, `attribute:<case attribute>`.
- Event dimensions: `activity`, `resource`, `month`, `week`, `weekday`, `hour`,
  `attribute:<event attribute>`. Use up to two dimensions.
- Measures (up to four): `count`, `cases`, `share`, and for cases `median`, `p90`, `avg`, `min`,
  `max` or `sum` of `duration_seconds`, `events` or `distinct_activities`.
- Case filters (`where`): `duration_over_seconds`, `duration_under_seconds`, `min_events`,
  `ends_with`, `lacks_activity`. Also available: `activity_in`, the KPI `cohort` filters and
  `case_type`.
- Results return at most 500 rows, with `rows_truncated` when there are more.

Variant dimensions use complete ordered activity arrays encoded as JSON strings
(`variant_encoding: ordered_activity_json_array`). A single activity named
`A > B` stays distinct from the two-activity sequence `A`, `B`; quotes, commas
and backslashes also retain their literal identity. These replace the earlier
display-separated strings, which could combine different paths.

Complete paths are built only when a variant dimension is requested, after
cohort and case filters. Other counts and duration summaries never materialize
unused path strings. Variant grouping preflights the actual UTF-8 JSON size:
at most 1 MiB per selected case path and 64 MiB across selected case paths.
An oversized selection receives an explicit error with no partial paths grouped;
choose narrower cases or another dimension. Memory exhaustion also returns a
safe error rather than an empty, partial or zero-valued result. The 500-row
display/export limit remains separate from these path-processing limits.

Saved analyses are stored with the log and deleted with it, at most 50 per log:
`GET|POST /v1/event-logs/{log_id}/analyses` and `POST …/analyses/{analysis_id}/delete`.
The dashboard's "Build an analysis" section runs, exports (JSON and CSV), saves and re-runs
them.
## Appending newer exports

`POST /v1/event-logs/{log_id}/append?format=…` (dashboard session plus the
`X-OWG-Local-Storage-Confirmed: true` header; same CSV mapping parameters as import) adds a
newer export to a saved log, so trends, KPIs and signals update without rebuilding the log.
In the dashboard, choose the file, confirm local storage and press "Add file to selected log".

- An event the log already holds (same activity, time, resource and linked objects) is skipped
  and counted in `duplicate_events_skipped`, so overlapping exports can be appended repeatedly.
- Appended events get a batch prefix on their identifier (`a1:`, `a2:`, …). Objects the log
  already holds keep their attributes; new objects are added.
- The whole append is one transaction. Malformed files, files without valid events, and
  identifier conflicts (for example an object ID reused with another type) leave the log
  unchanged.
- `GET /v1/event-logs/{log_id}/appends` lists each append's counts. The history is deleted with
  the log.

Appending one million events, half of them already in the log, takes about 12 seconds locally
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_append.py`).

## Compliance rules

`POST /v1/event-logs/{log_id}/compliance` (dashboard session only) checks cases against up to 20
declarative rules. These are Declare-style templates, as in ProM Declare, ABBYY Timeline or
Signavio process adherence:

| Template | Holds when | Applies to |
|---|---|---|
| `response` (`a`, `b`) | every A is eventually followed by B | cases containing A |
| `precedence` (`a`, `b`) | every B has an earlier A | cases containing B |
| `response_within` (`a`, `b`, `seconds`) | every A is followed by B within the limit | cases containing A |
| `existence` (`a`, `min`, `max`) | A occurs between min and max times | every case |
| `not_coexistence` (`a`, `b`) | A and B never occur in the same case | cases containing A or B |
| `segregation_of_duties` (`a`, `b`) | no single resource performs both (four eyes) | cases containing both |

```json
{"rules": [{"template": "segregation_of_duties", "a": "Create PO", "b": "Approve PO"},
           {"template": "response_within", "a": "Create PO", "b": "Approve PO", "seconds": 7200}],
 "cohort": {"started_from": "2024-01-01T00:00:00Z"}}
```

- Event order is time, then event ID.
- For each rule the result gives:
  - the cases the rule applies to (other cases satisfy it vacuously);
  - violating and satisfied cases;
  - the violation rate with a Wilson 95% interval;
  - up to 50 example cases with a short reason.
- Segregation of duties counts a case as `unknown`, never as satisfied, when either step has no
  recorded resource. No resource names are returned.

A rule can also be saved as a signal with `{"kind": "compliance", "rule": {…}}`. That signal
triggers on violating cases and reports new violations since the previous check, for example
after appending a newer export. In the dashboard, "Compliance rules" builds the rule list,
checks it and offers "Save as signal" for each rule.

Six rules over one million events take about 7 seconds locally
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_compliance.py`). These checks describe the
imported log against rules you entered. They are not verified policy, audit evidence or legal
compliance, and a violation may reflect missing events.

## Process model discovery

`GET /v1/event-logs/{log_id}/model-discovery` builds a draft process model from the log, in the
style of an inductive miner (as in PM4Py, ProM or Apromore). It takes the same cohort parameters as
the other analyses, plus:

- `noise` (0 up to, but not including, 1; default 0.2): leaves out infrequent behaviour. For each
  activity, directly-follows paths below this share of its strongest path are ignored, with ending
  the case counted as a path. Steps that fewer than this share of cases skip are treated as
  always done.
- `activities` (above 0, up to 1; default 1): keeps only the most frequent activities that cover
  this share of events.

How it works:

1. The most frequent 5,000 variants are used. Cases with more than 300 events are left out.
2. The log is split recursively at exclusive choices, sequences, parallel regions and loops found
   on the filtered directly-follows graph. Each part of the log is projected onto its own
   activities, so rare deviations are dropped rather than forced into the model.
3. When no split applies, one of two fallbacks is used and reported in `simplifications`:
   - an activity that occurs exactly once in every case is put in parallel with the rest;
   - otherwise the activities are drawn as a loop where they can occur in any order.

The result contains:

- `tree`: the process tree, with case counts on every choice and repeat counts on every loop.
- `process_model`: a catalog process model with start and end events, decisions with merges,
  loops and parallel split/join pairs. It is valid against the catalog schema, except that its
  business `goal` is empty. The reviewer states the goal before saving, and the catalog rejects
  a save without one.
- `quality`: how many cases were used and left out, and the share of checked cases the draft
  replays exactly over the activities shown. Checked cases are those in the 500 most frequent
  variants with at most 120 events.

Every decision branch has an unconfirmed rule that states only the observed share, for example
"Observed choice, not a confirmed rule: 54 of 60 cases (90%) continued with Approve". These
shares are not business conditions.

A catalog parallel region cannot contain decisions or loops, or more than eight branches. When
discovered concurrency needs more than that, it is drawn in its most common observed order if that
order still fits at least (1 − `noise`) of the cases. Otherwise it is drawn as an any-order loop.
Both cases are reported. Drafts larger than the catalog's bounds (40 nodes, 100 transitions,
40 rules) are refused with a request to lower `activities` or raise `noise`.

In the dashboard, "Discover a process model" has the two sliders, a plain-language outline of the
draft and "Review as catalog draft". That button opens the draft in the process catalog editor,
unsaved. After review it can be saved as a revision and exported as BPMN through the catalog.

One million events take about 2 seconds locally
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_model_discovery.py`). A discovered draft
describes the imported log. It is not a verified workflow or policy, and it never authorizes
execution.

## Process map with abstraction sliders

`GET /v1/event-logs/{log_id}/process-map?activities=0.8&paths=0.5` (dashboard session only)
returns a simplified directly-follows map, like Fluxicon Disco or Apromore. It takes the same
case filters as KPIs.

- **`activities`** (share of events, above 0 and up to 1): keeps the most frequent activities
  until they cover that share of events, at most 64. Hidden activities' events are removed
  *before* the map is computed, so cases are reconnected through the remaining steps. An arrow
  can therefore join steps that had hidden work between them.
- **`paths`** (share of transitions): keeps the most frequent transitions until they cover that
  share. It then adds back each visible activity's strongest incoming and outgoing path, so no
  activity is stranded. Start and end counts are always returned.
- Each transition carries its count, cases, and the median, mean and 90th-percentile elapsed gap.
- `coverage` reports the shown and hidden activities, and the event and transition shares
  shown.

The dashboard's "Process map" section has the two sliders (redrawn as you move them) and a
"Slowest paths" table. Gaps are elapsed time between recorded events, not measured work or
verified waiting. Redrawing a one-million-event log takes a few seconds
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_process_map.py`).

## Predicting open cases

`POST /v1/event-logs/{log_id}/predictions` (dashboard session only) estimates remaining time and,
optionally, an outcome risk for open cases. This is the local equivalent of ABBYY Timeline, IBM
Process Mining or Celonis predictive monitoring.

```json
{"end_activities": ["Close", "Cancel"],
 "outcome": {"kind": "duration_over", "seconds": 172800},
 "min_support": 20, "backtest_share": 0.3, "limit": 50}
```

- A case is open when its latest activity is not one of `end_activities`.
- Each open case is compared with completed cases that were in the same state: the same latest
  activity and the same number of events so far, with event counts above 10 grouped together.
- When fewer than `min_support` distinct completed cases match, the comparison falls back to
  the same latest activity, then to all completed cases. Cases without enough history are
  counted in `unsupported_open_cases` and not guessed.
- Each prediction gives the median remaining time and its 10th–90th percentile range. With an
  `outcome` (`duration_over` or `contains_activity`), it also gives the probability with a
  Wilson 95% interval.
- **Backtest:** the model learns from cases that ended before a cutoff (the most recent
  `backtest_share` of completed cases are held out) and predicts every step of the held-out cases.
- The backtest reports the mean absolute error against a naive baseline (the overall median
  remaining time) and how often the actual remaining time fell inside the 10th–90th percentile
  range (80% expected). With an outcome it also reports the Brier score against the base rate.

Remaining time is measured to the last recorded event. Predictions are estimates from this log's
history, not commitments or targets, and no resources are used. One million events take a few
seconds (`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_predictions.py`).

## Processing and waiting time

`GET /v1/event-logs/{log_id}/waiting-time` splits elapsed time into processing (an activity
instance's recorded start to end) and waiting (from the end of the case's earlier work to the next
start), like Disco, ProM or Celonis with start/complete data. It takes the case filters and:

- `mode`: `auto` (default), `lifecycle` or `end_attribute`.
- `lifecycle_attribute` (default `lifecycle:transition`, as in XES): per case and activity, the k-th
  `start` is paired with the k-th `complete`. A `complete` without a start, or an event without a
  lifecycle value, is an instant whose processing time is unknown. Other values (schedule,
  suspend, …) are counted and ignored, and starts without a completion are reported as still open.
- `end_attribute`: an attribute holding each event's end time with a UTC offset; the event time is
  the start. Values without an offset, or before the start, are counted and not used.

Overlapping work is never counted as waiting, and the case's busy time counts overlapping instances
once. The result has, per activity, processing and waiting-before medians, 90th percentiles,
totals and share of all waiting; the transitions with the most waiting; and flow efficiency (busy
share of elapsed case time). In `auto` mode without lifecycle values or an end attribute it returns
`available: false` and the attribute names found in the log.

For CSV logs, choose the lifecycle or end-time column under "Extra columns to keep" when importing.
Only selected columns are stored, with the same bounds as XES attributes. One million lifecycle
events take a few seconds (`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_waiting.py`).
Waiting comes from recorded timestamps, so it includes nights, weekends and unrecorded work, and no
time is attributed to a person.

## Process map comparison

`POST /v1/event-logs/{log_id}/map-comparison` with `{"cohorts": [first, second]}` (the same cohort
objects as the benchmark) compares the two cohorts' directly-follows maps, like Apromore's log
comparison:

- For every path (A → B) in either cohort: the share of each cohort's cases that contain it, the
  difference with a 95% interval, and whether it appears in only one cohort. Paths whose interval
  excludes zero come first.
- The median gap between A and B in each cohort. For the 25 most frequent paths shared by both
  cohorts, it also gives a percentile-bootstrap interval on the difference of medians (300 rounds,
  up to 1,000 sampled gaps per cohort).
- Both maps (up to 64 activities each), so the dashboard can draw them side by side under
  "Compare cohorts" → "Compare maps".

When the cohorts share cases, `note` says the intervals overstate certainty. One million events
compare in about a second (`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_map_comparison.py`).
Differences describe the two cohorts; they are not explained or caused by how the cohorts were chosen.
