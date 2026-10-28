# Event-log workspace (CSV, XES, OCEL 2.0)

For CSV exports with stable event identifiers, see [supplied source-event identities](SUPPLIED_SOURCE_EVENT_IDENTITIES.md). That optional mapping keeps distinct source IDs with identical payloads, refuses conflicting ID reuse atomically, and provides a separate Source-ID CSV round trip. Ordinary mappings retain the existing payload-based append behavior.

For directed OCEL object-to-object assertions, see [supplied object relationships](SUPPLIED_OCEL_OBJECT_RELATIONSHIPS.md). Import, append, local inspection and OCEL JSON/XML export preserve these separately from shared-event associations, without merging case histories.

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
| OCEL 2.0 JSON | yes | yes | Objects, object types, events, event-to-object links and supplied directed object-to-object relationships with qualifiers. Object attribute values keep their times (below). |
| OCEL 2.0 XML | yes | yes | The same retained content in the OCEL 2.0 worked-example XML layout (`.xmlocel`). Objects must precede events as in the original 2.0 printed root schema. See the relationship contract for schema/example errata and format limits. |

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

### What flattening onto one object type does

Every case analysis (variants, KPIs, maps, predictions, simulation) flattens the log onto the
chosen case notion: each object of that type becomes a case with a copy of every event linked to
it. `GET /v1/event-logs/{log_id}/object-flattening` (dashboard session only) reports, per object
type, how that changes the evidence, so case and event counts and cycle times are read with the
right caveats:

- **Deficiency:** events linked to no object of the type disappear (`events_left_out`, their share
  of the log and the activities they belong to).
- **Convergence:** events linked to several objects of the type are copied into each case
  (`convergent_events`, `flattened_event_rows`, `extra_event_rows`, `duplication_factor`, per
  activity), which inflates event counts and activity frequencies.
- **Divergence:** an activity repeated for one object of the type (`objects_with_repeats`,
  `repetitions`), with the other object types whose objects differ between the repetitions
  (`repetitions_differ_by`). A check repeated once per item of an order is item-level work, not
  rework of the order.
- **Qualifiers:** links per qualifier, and objects linked under more than one qualifier, a sign
  of a relationship that changes over the object's life.

The map shows these consequences in plain language under the selected object type. Explaining
repetitions examines at most five million links; beyond that `divergence_explanation_complete`
is false with a fixed reason, and the repetitions are still counted. Counts describe recorded
links, not verified business relationships. 160,000 OCEL events take about one second.

### Object attributes over time

In OCEL 2.0 every object attribute value has a time, and a value holds until the next value of
the same attribute; `1970-01-01T00:00:00Z` is the convention for an initial value without a known
time. The workspace keeps two things:

- the **stored value**: the first listed value per attribute, as before. Case attributes, filters
  and analyses use it;
- every **timed value**: each attribute's stored-value entry, plus at most 1,000 other values
  per object. Values whose time is missing or has no timezone are not kept. The import and
  append responses count them in `object_attribute_values`, with any values over the limit.

OCEL exports write the timed values with their own times, listing the stored value first, so a
re-import keeps both. A stored value without a timed value of its own (its time was missing or
had no timezone, or the log predates timed values) is written at the 1970 origin, never at an
observed time, so a re-import keeps it as the stored value and reports it as an initial value
without a known time. An attribute that only an append added to an existing object has timed
values but no stored value; OCEL cannot express that, so a re-import stores its earliest value. An
append adds timed values for new and existing objects (a newer export can carry later changes)
and skips values the log already holds. It keeps the same per-object limit, counting the values
the object already has, and reports the values it leaves out in `over_per_object_limit`. An existing
object also keeps at most 32 attribute names, stored or timed. Values of further new names are left
out and reported in `over_attribute_name_limit`. The stored value of an existing object is never
changed. Because the value limit excludes each stored-value entry and the name limit matches what
a re-import reads, every value an import or append keeps survives an OCEL export and re-import.
An OCEL export of an object that would not (more than 32 names) is refused before any file is
written.

`GET /v1/event-logs/{log_id}/object-attributes` (dashboard session only; optional `object_type`,
and `object_id` for one object's timeline of at most 200 values) reports, per object type and
attribute:

- `objects_whose_value_changes`, `value_changes` and the most frequent changes (`from` → `to`);
- initial values without a known time, and times with several different values (ambiguous);
- `event_links`: for every event linked to an object, the value at the event's time is compared
  with the stored value. A value recorded at the event's exact time applies to that event. The
  result counts links where the two differ, links before any value was recorded, and links at an
  ambiguous time.

The comparison examines at most five million event links. Beyond that,
`stored_value_comparison_complete` is false with a fixed reason; change counts stay exact. The map
summarizes changing attributes for the selected object type. 160,000 timed values on 160,000
events take under a second to analyze and add about 0.7 s to the import. The counts describe the
recorded values; they do not verify when the business change really happened. Discovery source
snapshots fingerprint events, objects and links. They do not yet include timed values, because no
analysis reads them.

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
| `GET /v1/event-logs/{log_id}/quality` | Stored quality and mapping reports per import and append, plus current whole-log diagnostics (below). |
| `GET /v1/event-logs/{log_id}/summary` | Events, cases, activities, time span and case-duration median/p90/mean. |
| `GET /v1/event-logs/{log_id}/object-types` | Object types, objects per type and shared events between types. |
| `GET /v1/event-logs/{log_id}/object-map` | Per-object-type lifecycle maps, links between object types and activities shared by several types. |
| `GET /v1/event-logs/{log_id}/object-relationships` | Supplied directed object assertions, with bounded type/qualifier groups. |
| `POST /v1/event-logs/{log_id}/object-relationships/query` | Literal endpoint/type/qualifier and incoming/outgoing filters, revision-bound keyset pages, current endpoint event counts. Does not merge cases. |
| `GET /v1/event-logs/{log_id}/object-flattening` | Per object type, what analysing it as the case leaves out (deficiency), copies (convergence) and turns into apparent repetition (divergence), plus qualifiers. |
| `POST /v1/event-logs/{log_id}/object-constraints` | Rules across object types: objects per event, related objects per object, and an activity only after a required activity of every related object. |
| `GET /v1/event-logs/{log_id}/object-attributes` | Per object type and attribute, how timed values change and how many event links happened while another value than the stored one held; `object_id` adds one object's timeline. |
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
| `GET /v1/event-logs/{log_id}/dotted-chart?time=absolute\|relative&order=start\|duration&max_cases=` | Events over time per case for a dotted chart (below). |
| `GET /v1/event-logs/{log_id}/drift?window=month\|week\|cases&window_cases=&min_cases=` | Change points in how cases run over time (below). |
| `GET /v1/event-logs/{log_id}/playback?activities=&max_cases=` | Per-case event offsets for animating cases along the process map (below). |
| `GET`/`POST /v1/event-logs/{log_id}/filter-sets`, `POST .../filter-sets/{set_id}/delete` | Saved, reusable case filters (below). |
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
M/D/1 and M/M/2 waiting times within 8%). It models no breaks inside working
periods, batching, priorities or abandonment. Results are model outputs under
the stated assumptions, not forecasts or measured outcomes, and authorize
nothing.

#### Checking the model against later cases

`POST /v1/event-logs/{log_id}/simulation/validation` (dashboard session only) tests whether the
calibrated model reproduces a later period it has not seen:

```json
{"holdout_share": 0.3, "distribution": "exponential", "cases": 2000, "seed": 1}
```

- The cutoff is the start time below which `1 - holdout_share` of the cases started (0.05–0.5).
- The model is calibrated only from cases whose every event is at or before the cutoff. Cases that
  span the cutoff are left out of both sides and counted.
- It is simulated with those parameters and compared with the cases that started after the
  cutoff. The comparison covers median, 90th percentile and mean cycle time with relative errors,
  the calibrated and observed arrival rates, and a two-sample Kolmogorov–Smirnov distance with its
  5% critical value (`distributions_differ_at_5pct`).
- At most 20,000 held-out cases are sampled for the distance; quantiles use all of them.
- Held-out cases still running when the log ends contribute their duration so far.
- A `calendar` or entered `arrival_pattern` can be included. A pattern measured from the log is
  refused, because it would include the held-out period.

The tests check three cases. A stable process reproduces its later period exactly. A process whose
second activity takes three times as long after the cutoff is flagged (KS distance 1). Exponential
activity times expose the calibration's known simplification: the median gap is used as the
exponential mean, so simulated times come out about 1 − ln 2 ≈ 31% short. Agreement on one period
is evidence about that period, not a forecast guarantee. The dashboard's simulation section has
"Check against later cases".

**Measured time per activity.** Calibration reports both the median gap (`minutes`, robust to
outliers) and the mean gap (`mean_minutes`) before each activity. `"activity_time_basis": "mean"` in
a scenario or validation uses the mean for every measured activity in both runs. The mean is the
correct parameter for variable (exponential) times and does not shorten skewed ones. The default
stays `median`, so existing results are unchanged; explicitly entered minutes always win. The tests
check that the mean basis brings the held-out error for exponential times within 12%, against about
31% with the median, across three seeds. The dashboard's simulation form and held-out check both
offer "Measured time per activity"; switching it refills the measured minutes and leaves activities
added by hand untouched.

#### Step-by-step routing

Path routing replays the most frequent complete case paths (at most 10 by default), so cases on rarer
paths are left out of the calibration. Where variants multiply, for example optional steps in many
combinations, the frequent paths are also the short ones and the simulation runs short.
`"routing": "transitions"` in a scenario or held-out validation instead learns from every eligible case:

- **What it learns:** which activity starts a case and, after each activity, which activity follows
  or whether the case ends.
- **How cases move:** each simulated case walks those shares one step at a time, so loops repeat as
  often as the log suggests and combinations never seen as a whole can still occur.
- **Limitation:** routing is first-order, so the next step depends only on the current activity.
- **Calibration:** `GET …/simulation/calibration?routing=transitions` returns `transitions`, with
  start counts and, per activity, the counts and shares of the next activity (`to: null` ends the
  case). Arrivals and activity times then also come from every eligible case.
- **Changing routing:** a scenario changes routing with `"transitions": [{"from": "Check", "to":
  [{"activity": "Close", "weight": 0.95}, {"activity": "Fix", "weight": 0.05}]}]`. Each row replaces
  one activity's next steps; `"from": null` replaces the start. New activities need minutes. Paths
  and transitions cannot be combined.
- **Results:** both runs report `visits_per_case`. A case that reaches the 100-step path limit is
  stopped there and counted in `routes_cut_at_path_limit`.
- **Held-out validation:** with step-by-step routing, validation reports `simulated_routes`. It gives
  the simulated cases and how many stopped at the path limit. Stopped routes are compared as if they
  had finished, which shortens the simulated times, and the fit metrics then do not assess
  completed routes. Any stops are stated in the assumptions and the dashboard, which also notes that
  routing is first-order.
- **Random numbers:** each case walks with its own random numbers, so an unchanged scenario simulates
  identically and changing times never changes routes.
- **Uncertainty:** replications draw each row of shares from a Dirichlet distribution over its
  counts.

The tests check two cases:

- **Rework loop:** in a loop where Fix follows Check with probability 0.3, simulated visits per case
  match the geometric expectation (Fix 0.43, Check 1.43).
- **Optional checks:** on a log of six optional checks, a held-out validation with path routing runs
  about 28% short on the mean. Step-by-step routing matches the median exactly, keeps the mean within
  15% and halves the distribution difference.

The dashboard's simulation form and held-out check offer "How cases move between activities" and
"Routing". With step-by-step routing, the routing text box is hidden and visits per case are shown.

#### Uncertainty from routing and arrivals

A what-if run uses the routing shares, arrival rate and activity times measured from the log as if
they were exact. With few cases behind a branch they are not. `"uncertainty": {"replications": 2–50}` in a
scenario repeats the paired baseline and scenario simulation with parameters drawn from what the
log supports:

- **Routing:** shares come from a Dirichlet distribution over the observed path counts, a Bayesian
  bootstrap of which path each case took. A branch seen three times varies far more than one seen
  three thousand times.
- **Arrival rate:** drawn from a Gamma distribution over the number of selected case starts and
  their time span.
- **Activity times:** each measured time is scaled by a Gamma draw with as many observations as gaps
  it was measured from (`observed_gaps` in the calibration). This is exact for the mean of
  exponential times; the median and mean scale together. A time seen five times varies far more
  than one seen five thousand times, and a first activity with no gap is not drawn.
- **Fixed values:** paths, `arrivals_per_hour` and minutes you entered stay as entered. Resources
  and the process structure are not varied, and the intervals leave out any change in the process
  that the log does not show.
- **Random numbers:** each replication draws its own simulation random numbers and shares them
  between baseline and scenario. The intervals include simulation noise, and the change stays
  paired: an unchanged scenario shows no change in any replication.

The point results are unchanged. The response adds `uncertainty`, giving:

- median and 95% ranges of the p50 and p90 cycle times for both runs and their change;
- how many replications make the scenario's median faster, and how many overload an activity;
- each measured path's observed case count and drawn share;
- each measured activity's gap count and drawn minutes;
- the drawn baseline arrival rate.

Replications × cases × 2 runs may not exceed 400,000. The tests check the drawn share against its
Beta distribution and the drawn times against their Gamma spread. They check that the 95% ranges
cover the true routing share, and the true mean of exponential times from 30 gaps, in 90–99% of
300 simulated logs. With 40 cases the long branch's range is more than three times as wide as with
2,000.

In the dashboard, "Uncertainty replications" adds interval tables. Routing, arrivals and minutes
still as measured are left to the server to draw; values you edited stay as entered.

#### Shared resource pools

By default each activity has its own resources. A scenario can instead let several activities share
one team:

```json
{"pools": {"Review team": {"resources": 3, "activities": ["Check", "Approve"]}}}
```

A pool's activities are served by one team of interchangeable resources from a single first-come,
first-served queue, so a person who finishes a Check can take the next waiting Approve. A pooled
activity's own resource count is not used; each activity is in at most one pool, every pooled
activity must be on one of the scenario's paths, and up to 20 pools are allowed. Pools change the
scenario only; the measured baseline keeps one dedicated pool per activity. Results add a row per
pool (people, utilization, offered load, mean and 90th-percentile wait) and mark each pooled
activity with its pool, whose utilization and offered load it reports. The test suite checks that
pooling two dedicated M/M/1 queues at load 0.5 (mean wait 1 h each) into one M/M/2 queue gives the
Erlang C wait of 1/3 h. Each queue serves waiting work in order of arrival at the step (ties in event
order), so a case returning for a later step is not overtaken by newer arrivals; multi-step and
re-entrant routes are checked against an independent first-come, first-served reference. The
dashboard's simulation form defines one shared pool.

#### Seasonal arrivals

Without a pattern, cases arrive at a steady rate around the clock. An `arrival_pattern` makes the rate
vary by local hour of week and month in one IANA time zone, for both runs (demand is the same);
`scenario_arrival_pattern` replaces it for the scenario only:

```json
{"arrival_pattern": {"timezone": "Europe/Berlin",
  "hourly": [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0],
  "daily": [1, 1, 1, 1, 1, 0, 0], "monthly": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2]}}
```

`hourly` (24 weights, midnight first), `daily` (7, Monday first) and `monthly` (12) are relative
weights, each optional, multiplied together and normalized so that a week, and a year, of arrivals
average exactly `arrivals_per_hour`: the stated rate stays the long-run average, and only its timing
changes. `{"from_log": true, "timezone": "Europe/Berlin"}` instead measures the hour-of-week pattern
from the start times of the selected calibration cases: starts per local hour of week, divided by how
often that hour occurs in the whole local days spanned. It needs at least 14 days of case starts, uses
15-minute resolution, does not measure months, and reports its basis (case starts, days, hours of week
without starts). The time zone defaults to the run's calendar zone, else UTC. A run has one clock:
with a calendar, the pattern starts at the calendar start, and an explicit `start` must be that same
instant (any offset or zone); without one, `start` sets the clock.

Arrivals form a non-homogeneous Poisson process, drawn exactly by inverting the cumulative rate, which
is constant within each UTC quarter hour (every real UTC offset and clock change falls on one), with
one random number per case, so runs with and without a pattern stay paired. Daylight-saving changes
follow the zone's rules. Results add the applied pattern (normalized factors and the peak factor) and
the simulated arrivals by local hour and weekday. Offered load and the overload flag still use the
long-run average rate, so queues can build during peaks even when no activity is flagged. The tests
check that office-hour weights put every arrival in those hours, that 12 open hours double the rate
while open (giving the M/M/1 wait at that rate), and that a measured pattern recovers weekday office
hours across a clock change. At most 10 years of simulated arrivals are allowed with a pattern; pattern
and calendar start dates must fall between 1971 and 2200, and a run whose clock would pass year 9999
is refused rather than failing.

#### Working calendars

Without a calendar, resources work around the clock. A `calendar` limits every
resource to working periods in one IANA time zone, for both runs:

```json
{"calendar": {"timezone": "Europe/Berlin",
              "hours": {"mon": [["09:00", "17:00"]], "tue": [["09:00", "12:00"], ["13:00", "17:00"]]},
              "holidays": ["2025-12-25"], "start": "2025-01-06T00:00:00"},
 "scenario_calendar": {"timezone": "Europe/Berlin", "hours": {"mon": [["08:00", "18:00"]]}}}
```

Days not listed are closed; each day takes up to four non-overlapping periods
(`24:00` ends a day), holidays close whole local days, and `start` (local time,
default Monday 6 January 2025) anchors the simulated clock. `scenario_calendar`
changes the scenario only, for example to test a Saturday shift. Cases keep
arriving around the clock. A case can reserve a free resource outside working
time but work starts at the next opening, pauses when a period ends and
resumes at the next one; waiting counts from arrival to the start of work.
Local hours are converted with the time zone's rules, so a daylight-saving
change moves them in UTC exactly as clocks do. A period covers exactly the real
moments whose local clock time falls inside it: a clock time skipped when clocks
go forward maps to the moment of the jump (a period inside the skipped hour has
no working time; one starting inside it begins at the jump), a clock time
repeated when clocks go back counts from its first occurrence at a period's start
to its last occurrence at its end, and periods with no real length are dropped. Utilization is measured against
working time, and offered load spreads arrivals over the average weekly
working share (holidays are not counted there), so a load of 1 or more still
flags a queue that keeps growing. Responses describe each run's calendar. Time zone rules come from the
operating system where it has them and otherwise from the bundled `tzdata`
package, which is a base dependency so Windows and slim installs work. The
test suite checks exact pause-and-resume times, a holiday, periods inside,
across and partly in Berlin's skipped and repeated hours, that work always
starts inside a usable period, and that a round-the-clock calendar reproduces
the results without one.

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

### Case metrics

A case-level analysis can define up to four typed case metrics in `metrics` and measure them with
`{"fn": "median", "of": "metric:<name>"}` (also `p90`, `avg`, `min`, `max`, `sum`). Each metric has a
name (`a-z`, digits, `_`) and one of these kinds:

| `kind` | Fields | Value per case | Unit |
| --- | --- | --- | --- |
| `time_between` | `from_activity`, `to_activity`, `from_occurrence` and `to_occurrence` (`first` or `last`) | Seconds between the chosen recorded events | seconds |
| `activity_count` | `activity` | Number of events of that activity (0 when none) | events |
| `event_attribute_sum` | `attribute`, `unit` | Sum of the numeric values of an event attribute | as declared |
| `case_attribute_number` | `attribute`, `unit` | A numeric case attribute | as declared |
| `arithmetic` | `operation`, `left`, `right`, `expression_version: "1"` | Add/subtract/multiply/divide earlier metrics or finite typed constants | checked symbolic units |

A case without a value (an activity that never happens, the "to" activity coming first, or no
numeric value) is left out of that metric's measures, and the result counts those cases per
metric, by reason for `time_between`. Coverage counts describe the cases the analysis selects,
after its case filters. NaN, infinite values and per-case sums that overflow count as missing
(`cases_with_non_finite_value`), and an aggregate that overflows is returned as unavailable and
counted in `non_finite_results_withheld`; results never contain NaN or infinity. Metrics are
referenced only as `metric:<name>`. Units are labels you declare, such as EUR or kg: values are
never converted or summed across units. The result's `units` maps every measure column to its unit.
Like every analysis, metrics compile to parameterized SQL from the fixed vocabulary.

Metric definitions can be saved with the log for reuse (`openworkgraph.event-log-case-metric.v1`,
at most 50, unique names): `GET|POST /v1/event-logs/{log_id}/metrics` with `{"metric": {...}}` and
`POST …/metrics/{metric_id}/delete`. Saved analyses keep the full definitions they used, so they
rerun the same way after a library metric is deleted. In the dashboard, "Case metrics" under "Build
an analysis" defines, adds and saves metrics, and lists the saved ones.

[Arithmetic metrics](EVENT_LOG_ARITHMETIC_METRICS.md) keep the same four-metric limit, reject
forward/cyclic references and incompatible addition units, and disclose zero/missing/overflow
coverage. Units are never converted. Saved arithmetic library entries are symbolic templates;
save a complete analysis to retain their dependency definitions. JSON exports retain the spec,
units and coverage; CSV exports remain numeric tables.

## Appending newer exports

`POST /v1/event-logs/{log_id}/append?format=…` (dashboard session plus the
`X-OWG-Local-Storage-Confirmed: true` header; same CSV mapping parameters as import) adds a
newer export to a saved log, so trends, KPIs and signals update without rebuilding the log.
In the dashboard, choose the file, confirm local storage and press "Add file to selected log".

- An event the log already holds (same activity, time, resource and linked objects) is skipped
  and counted in `duplicate_events_skipped`, so overlapping exports can be appended repeatedly.
- Appended events get a batch prefix on their identifier (`a1:`, `a2:`, …). Objects the log
  already holds keep their stored attributes; new objects are added. Timed OCEL attribute values
  are added for both, skipping values already held (see Object attributes over time).
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

### Object rules across object types

An additional [supplied-relationship shape rule](SUPPLIED_OBJECT_CONSTRAINTS.md)
checks distinct direct neighbors in the separately retained OCEL assertion graph,
including zero-event objects, with literal direction/qualifier filters and exact
relationship/object-population digest pins. It does not infer case membership,
temporal validity or verified business compliance. The existing templates below
continue to use event links and shared-event associations.

Compliance rules check one case notion at a time. `POST /v1/event-logs/{log_id}/object-constraints`
(dashboard session only) checks up to 20 rules about how object types relate, without flattening
the log onto one of them:

| Template | Checks | Counted per |
| --- | --- | --- |
| `event_cardinality` (`activity`, `object_type`, `min`, `max`) | every event of the activity is linked to that many objects of the type, for example every `Pay` to exactly one `invoice` | event |
| `object_cardinality` (`object_type`, `related_type`, optional `via_activity`, `min`, `max`) | every object of the type is related to that many objects of the other type, for example every `order` to one to ten `item`s | object |
| `synchronization` (`activity`, `object_type`, `related_type`, `required_activity`) | every event of the activity linked to an object of the type comes after an event of the required activity for each related object of the other type, for example an `order` ships only after each of its `item`s is picked | event and object pair |

- **Related objects:** two objects are related when they share an event, or only an event of
  `via_activity` when one is given. They are related through any shared event, earlier or later.
- **Order:** events are ordered by time, then event ID. A required event at the same time with a
  later event ID does not count as earlier.
- **Vacuous rules:** a rule whose trigger never occurs is satisfied vacuously and counted
  separately. So is a synchronization with no related objects. The response says whether each
  named activity and object type occurs in the log.
- **Results:** each rule reports `activated`, `violating`, `satisfied`, a violation rate with a
  95% Wilson interval, and up to 50 violating events, objects or pairs with a short reason, such
  as `3 related` or `1 related without it, 1 only later`.
- **Work budget:** relating objects examines at most five million object pairs per rule. Beyond
  that the rule reports `complete: false` with a fixed reason, never partial counts.

In the dashboard, the object-centric map adds "Object rules" when the log has more than one
object type. 100,000 OCEL events with 20,000 orders take under a second for three rules. Results
describe recorded links, not verified business relationships, audit evidence or legal compliance.

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

### Still-open cases and censoring

Learning only from completed cases is biased: when an export ends, the cases still open are
disproportionately the long ones, so completed history under-represents long remaining times, and
the most recent period most of all. The default estimator also ignores how long an open case has
already waited since its latest event. `"estimator": "survival"` corrects both:

- Every case contributes. A completed case's earlier events give an observed remaining time; an
  open case's events give a lower bound (it had not finished when the log ends), a right-censored
  observation. A Kaplan–Meier estimator per comparable state, with the same fallbacks, turns both
  into a survival curve. `censoring.open_cases_used_as_censored` reports how many open cases were
  used.
- Each open case's estimate is conditioned on its idle time: the remaining time **from the end of
  the log** (`remaining_measured_from: "end_of_log"`) has quantiles where S(idle + t) / S(idle)
  reaches 1 − q.
- When too many comparable cases are still open for a quantile to be reached, the estimate is
  withheld (`estimate_withheld`, `withheld_estimates`) and `remaining_at_least_seconds` gives the
  lower bound the history supports, rather than a guess. Withheld estimates are listed first.
  Each prediction reports `comparable_cases_still_open`.
- **As-of backtest:** the log is replayed as if it ended at the cutoff. The model learns only what
  was known then (cases open at the cutoff are censored there) and predicts the cases in progress
  at the cutoff. Those that have finished since are scored, and the completed-cases estimator is
  scored on the same cases for comparison (`completed_cases_estimator`,
  `improvement_over_completed_cases_estimator`). Cases still open are counted
  (`still_open_unscored_cases`, and `still_open_already_beyond_p90` for those already past their
  90th percentile), not dropped silently. Scored cases are the ones that finished, so the errors
  still favour short remaining times; the result says so.
- Outcome risk still uses completed cases only, and the result says so.

The tests check the curves against a textbook product-limit calculation. In a log where cases
last an exponential time with a mean of 50 hours and long ones are still open at export, survival
medians are within 15% of the true 50 · ln 2 hours for every open case; completed cases alone give
under 40% of it. The as-of backtest shows the survival estimator with at least 15% lower error than
completed cases alone, and 10th–90th percentile coverage near the expected 80%. One million events
with both estimators take about 18 seconds, including the import, in the scale acceptance.

## Processing and waiting time

An optional [declared working-calendar lens](WORKING_CALENDAR_ANALYSIS.md) adds schedule-adjusted
aggregates alongside elapsed metrics, with explicit assumptions and guarded local downloads.

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
share of elapsed case time). A waiting hotspot's source is the earlier instance with the latest
recorded end, using the same instance frame as the waiting amount. For example, A=[0h,10h],
B=[1h,2h], C=[20h,21h] has a 10-hour A→C boundary gap; B's later start does not make it
that gap's source. Tied latest ends choose the latest start, then the greatest activity label
in lexical order as a representative. This deterministic timestamp boundary is not a causal handoff or an employee
performance measure. Processing, waiting amounts and union-busy accounting are unchanged.
In `auto` mode without lifecycle values or an end attribute it returns `available: false` and
the attribute names found in the log.

For CSV logs, choose the lifecycle or end-time column under "Extra columns to keep" when importing.
Only selected columns are stored, with the same bounds as XES attributes. One million lifecycle
events take a few seconds (`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_waiting.py`).
Waiting comes from recorded timestamps, so it includes nights, weekends and unrecorded work, and no
time is attributed to a person. Editing the lifecycle or end-time attribute clears the old result;
a response for earlier settings cannot overwrite a later analysis. The overlap-boundary regression
uses independent interval oracles and the reproducible synthetic `scripts/benchmark_waiting_boundary.py`
in lifecycle/end-attribute modes at 100,000 and 1,000,000 events. Those checks establish supplied-log
accounting, not a latency SLA, source completeness or causal process/human performance.

## Dotted chart

`GET /v1/event-logs/{log_id}/dotted-chart` returns every case as a row of events over time, as in
Disco or ProM. It takes the case filters and:

- `time`: `absolute` (calendar time from the first event, to see arrival waves, batches and
  backlogs) or `relative` (time since each case's first event, to compare how cases unfold).
- `order`: rows by case start, or longest cases first.
- `max_cases` (1–1000, default 300): rows are sampled evenly across the ordered cases, so the
  chart keeps its shape. At most 30,000 dots are returned, thinned evenly within long cases.
  `coverage` says when either happens.

Colours follow each activity's event count in the whole log, not in the filtered view, so filtering
never repaints an activity. The seven most frequent activities get their own colour from a
colour-blind-checked palette and the rest share "Other". The dashboard section draws the chart
with a hover tooltip (case, activity, time), a legend with event counts and a table view of the
same cases. One million events take well under a second to sample
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_dotted_chart.py`).

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

## Changes over time (concept drift)

`GET /v1/event-logs/{log_id}/drift` finds when the way cases run changed, like Apromore's or ProM's
drift detection. It takes the case filters and:

- `window`: `month` (default), `week`, or `cases` with `window_cases` (20–100,000) cases per
  window, grouped by when each case started.
- `min_cases` (default 30): a boundary is only tested when both windows have at least this many cases.

Each window's behaviour is its directly-follows profile: for every path A → B, how many of the
window's cases contain it (up to 600 sampled cases per window). Adjacent windows are compared with
the Jensen–Shannon distance (0 identical, 1 disjoint) and a permutation test (99 random splits of
the same cases, keeping each side's size). When the observed distance beats every permutation,
the p-value comes from a normal tail fitted to the permutation distances, so many boundaries can be
tested; `p_value_method` says which was used. A boundary is a change point when its p-value is below
0.05 divided by the number of boundaries tested (Bonferroni) and the distance is at least 0.05.

For each change point the result lists the five paths whose case share moved most. Every window has
its case count and median case duration. A change that starts mid-window usually shows up on both
sides of that window. One million events take a few seconds
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_drift.py`). A change point does not say why
behaviour changed, and a change in how events are recorded looks the same.

## Data quality and mapping reports

Every import and every appended batch stores a counts-only quality report next to the log. The
import and append responses include a short summary as `quality`, and
`GET /v1/event-logs/{log_id}/quality` returns every report (batch 0 is the import) together with
the current diagnostics and content digest of the whole log.

Each report records:

- **Transform**: the source format, the column mapping (case, activity, timestamp, resource and the
  extra columns kept, in order) or the standard mapping for XES/OCEL, the mapping version
  (`owg.event-log-mapping.v1`) and the importer version, with `transform_sha256` over that
  definition.
- **Digests**: `source_sha256` of the uploaded bytes and `content_sha256` of what was stored. The
  content digest is order-independent: one SHA-256 over the sorted per-event hashes and one over
  the sorted event-object links. Importing the same file with the same mapping again gives the same
  three digests. Reordering the kept columns changes the transform and content digests, because
  attributes are stored in the declared order.
- **Rows**: accepted and rejected rows. For CSV the rejections are broken down into missing case,
  missing activity, missing time, time without a UTC offset and unreadable time; one row can count
  under several reasons. XES and OCEL readers only report a total, and the report says so. Rejected
  rows are not kept unless you choose to keep them (below). Appends also record how many events were skipped as already present.
- **Diagnostics** (for the batch; the endpoint also gives them for the whole log):
  - duplicate events: same case objects, activity, time and resource;
  - events sharing a timestamp with another event of the same case, and the number of such cases.
    Their order falls back to the event identifier, not to when they happened;
  - events without a resource;
  - events without a case object;
  - single-event cases per case notion;
  - events dated after the import;
  - for up to 32 attributes, how many events have a value.
- **Completeness**: always `unknown`. The counts describe the supplied rows only; whether the
  source system holds more history is not known.

Reports contain counts, column names and digests, never case IDs, activity labels, resources or
attribute values. They are deleted with the log. The report is written in its own short
transaction after the events commit, so a failure to write it never undoes an import. Logs imported
before reports existed, or batches whose report could not be written, appear in
`batches_without_report`.

Writing a report adds about 2 seconds per million events to an import. In the dashboard, the
"Data quality" section under the overview shows the reports, mapping and diagnostics, and the
import status line names the rejection reasons.

## Rejected rows: keep, release or discard

By default rejected rows are dropped and only counted. For CSV you can instead choose to keep them
with the log (`keep_rejected=true` on import or append, or the checkbox under the import form). Up
to 10,000 rejected rows per batch are kept with their mapped values (case, activity, the time as
supplied, resource and kept attribute columns) and the reasons they were rejected; the quality
report says how many were kept and how many were over that limit. Kept rows stay on this computer
and are deleted with the log.

| Route | Purpose |
| --- | --- |
| `GET /v1/event-logs/{log_id}/quarantine?batch=&after=&limit=` | Kept rows by batch and reason, a page of rows, and every decision taken. |
| `POST /v1/event-logs/{log_id}/quarantine/release` | `{"naive_timestamp_offset": "+01:00", "batch": 0}` (batch optional). |
| `POST /v1/event-logs/{log_id}/quarantine/discard` | `{"batch": 0}` (batch optional) deletes kept rows. |

A release applies one explicit decision: the UTC offset to assume for times written without one
(`Z` or `±HH:MM`, at most ±14:00). The kept rows that become valid under it are appended as a new
batch through the normal append path, so events already in the log are skipped, and that batch
gets its own quality report (`kind: quarantine_release`) whose transform records the offset. Rows
that still fail, such as rows without a case, stay kept. Releasing does not check that the offset
is right, and nothing is released automatically. Every release and discard is recorded with its
counts. Appending and removing the released rows are two steps; if the second is interrupted,
releasing again adds nothing new.

The same decision can be made up front for a CSV import or append with
`naive_timestamp_offset=+01:00`. It is part of the transform and its digest, and the report counts
the times read with the assumed offset.

## Case filter and playback

The **Case filter** keeps only cases whose recorded order matches every rule, like Disco's follower
filter or Celonis' process-flow selection. A rule names two activities and one of four orders:
*is directly followed by*, *is eventually followed by*, *is never directly followed by* and *is never
eventually followed by*. "Eventually" means some occurrence of the first activity comes before some
later occurrence of the second. Events that share a timestamp are ordered by event identifier, as
everywhere else. Up to five rules can be combined.

The filter is the `follows` query parameter, a JSON list of
`{"from": "A", "to": "B", "mode": "direct" | "eventual", "negate": false}` rules. It is accepted
alongside the other case filters (`started_from`, `started_before`, `resource`, `activity`,
`attribute`) by `summary`, `variants`, `activities`, `kpis`, `process-map`, `playback`,
`dotted-chart`, `waiting-time`, `drift`, `model-discovery` and `organization`. Filtered responses say
which filter was applied and how many cases the log had before filtering. In the dashboard the
overview shows "Filtered: N of M cases"; sections not in that list still use every case, and the
filter note says so.

**Saved filters.** The active case filter can be saved under a name and applied again later from
the same panel; applying it re-runs every filtered view at once. The API stores any case filter
(`{"name": "...", "cohort": {"follows": [...], "started_from": "...", ...}}`, up to 50 per log,
names unique regardless of case) as `openworkgraph.event-log-filter-set.v1`; the dashboard panel
applies follower rules. A saved filter is re-applied to the log as it is now, so the listing says
when newer exports were appended after it was saved and names any activities it uses that the log
does not contain. Saved filters are deleted with the log and never change events.

The case evidence drill-down also uses the active case filter, so browsing cases shows the same
cases the filtered views count ("N matching cases (case filter applied)"). Object-type maps keep
their own case notion and are not filtered.

**Case playback** animates cases over the process map. `GET /v1/event-logs/{log_id}/playback` returns
each case's events as seconds from the log's first event, restricted to the same most frequent
activities that the map shows for the same `activities` share. At most `max_cases` (default 200, up
to 500) cases are returned, sampled evenly by start time, and at most 30,000 events in total. The
dashboard moves one token per case along the drawn arrow between consecutive events. A step whose
arrow the paths slider hid moves in a straight line, and steps that share a timestamp jump instantly;
the status line counts both. Play, pause and the position slider work from the keyboard. With reduced
motion the playback advances in steps once per second. Tokens show recorded timestamps, not work
being done, and nothing is executed. A million events filter and play back in a few seconds
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_playback_filters.py`).

## Share a minimized aggregate snapshot

After running a local analysis, **Prepare sharing snapshot** recomputes and previews a bounded
numeric artifact with coded group labels, units, aggregate definitions, coverage and source/spec
fingerprints. Nothing is sent to an organization until you explicitly download, review and publish
the file through its Shared analyses portal. [Shared analyses](SHARED_ANALYSES.md) documents
separate scopes, per-view roles, revisions, expiry, revocation, audited export and the remaining
shared-dataset limitations. Codes minimize labels; they do not anonymize the numeric information.

The builder supports [cancellable local analysis jobs](LOCAL_ANALYSIS_JOBS.md),
with bounded queues, deadlines, session-scoped status/results and explicit cancellation.

Selected Jira issues can also be read manually into a retained local source-ID log with separate read/retention/label consent. See [Retained Jira ingestion](RETAINED_JIRA_INGESTION.md) for scope, lifecycle, checkpoint, pagination and completeness limits.
