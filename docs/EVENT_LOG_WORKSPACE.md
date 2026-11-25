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

## Try it with a sample log

No export of your own yet? **Try with a sample log** stores and analyses a
synthetic order-to-cash log (`POST /v1/event-logs/sample`). It needs the
dashboard session and the same "store on this computer" confirmation as an
import.

**What's in it:**

* 400 orders with items, deliveries, invoices and payments, as an OCEL 2.0
  object-centric log;
* the activity names of the order-to-cash domain pack;
* patterns to find:
  * waiting for payment after the invoice;
  * order changes (rework);
  * credit holds that add days;
  * slower picking in the most recent orders;
  * some invoices still unpaid.

**Where it comes from:** it is generated on this computer from a fixed seed,
so it is the same every time and contains no real customers, people or
amounts.

**How it is stored:** it is saved as *Sample: order to cash (synthetic)* and
opened with *order* as the case notion. Asking again opens the existing
sample rather than adding a copy. It is deleted like any other log.

## Formats

| Format | Import | Export | Notes |
| --- | --- | --- | --- |
| CSV | yes | yes | You map the case, activity, timestamp and (optional) resource columns. |
| Excel (.xlsx) | yes | no | One worksheet is converted to CSV on this computer, then mapped and imported like any CSV (below). |
| XES (IEEE 1849) | yes | yes | `concept:name`, `time:timestamp` and `org:resource`; up to 32 other attributes of 500 characters each are imported as strings. Choose **XES with retained attributes** to include static trace and event attributes in the export. |
| OCEL 2.0 JSON | yes | yes | Objects, object types, events, event-to-object links and supplied directed object-to-object relationships with qualifiers. Object attribute values keep their times (below). |
| OCEL 2.0 XML | yes | yes | The same retained content in the OCEL 2.0 worked-example XML layout (`.xmlocel`). Objects must precede events as in the original 2.0 printed root schema. See the relationship contract for schema/example errata and format limits. |

The attribute export uses the selected case notion and includes values in the
download, including supported lifecycle fields. The standard XES choice exports
only the structural fields. XES uses flat traces, so use OCEL to retain object
relationships and timed object attributes. The attribute export refuses data
that would require changing a retained value, exceeding XES import bounds, or
overwriting a reserved field. The local dashboard export endpoint accepts
`format=xes-attributes` and returns a `.xes` file.

Every log is stored object-centrically. A flat CSV or XES log becomes one
object type, `case`. For an OCEL log you choose the case notion (`order`,
`item`, ...) per question, and the overview reports which object types share
events.

Timestamps must carry a timezone (`Z` or an offset). Rows without one, or with
an unparseable time, are counted as rejected and never guessed, unless you
state the offset such times use ("Offset for times without one" in the CSV
mapping, `naive_timestamp_offset=+01:00` in the API). That choice is recorded
with the import. XML with a `DOCTYPE` or entity declaration is refused before
parsing.

### Compressed files

Any import or appended file may be gzip-compressed, as public logs such as the BPI Challenge
`.xes.gz` files are. The server recognises gzip by its first bytes and decompresses the upload before
reading it, bounded by the same 4 GB limit as an uncompressed file, so a small file cannot expand
without limit; a damaged archive is refused. The dashboard picks the format from the name without
`.gz` (`orders.xes.gz` is XES) and reads the columns of a `.csv.gz` file in the browser. The quality
report's digests describe the decompressed bytes, so the same log compressed or not counts as the same
file.

### Timestamp formats

ISO 8601 times (`2024-03-04T14:05:00Z`, `2024-03-04 14:05:00+01:00`) need no
setting. For other layouts, such as `13/03/2024 14:05`, `04.03.2024 14:05:00`
or `04-MAR-24 02.05.00 PM`, declare the format of the timestamp column as a
strptime pattern ("Timestamp format" in the CSV mapping, `timestamp_format=` in
the API). Patterns may use `%d %m %Y %y %H %I %M %S %f %p %b %B %z`, with
spaces and `/ . - : , T` between them. They need a year, a month and a day,
and `%I` needs `%p`.

Every value must match the pattern exactly. A value with trailing text, an
impossible date such as 31/02, or a value in another layout is rejected as
unparseable, not guessed. A pattern without `%z` describes times without an
offset, so the import also needs the offset decision, or it is refused before
anything is read. The pattern is recorded in the import's transform and its
digest.

After you choose a file, the dashboard sends the first 256 KB of the file to
this computer's server (`POST /v1/event-logs/timestamp-formats?column=...`;
nothing is stored). The server tries the known layouts on up to 500 values of
the timestamp column and preselects a format that reads them all. When every
day in the sample is 12 or less, day-first and month-first both fit; the
dashboard then lists both with how the first value would read, and you choose.
Quarantined rows are released with ISO parsing only, so re-import a file that
needs a format.

### Reusing an earlier mapping

When a CSV file's header has every column an earlier import mapped (case,
activity, timestamp and, if one was mapped, resource), the dashboard offers
up to five compatible mappings across comma, semicolon, tab and pipe
delimiters. The selected log’s compatible mapping appears first, followed by
the newest other matches. Applying one fills in the delimiter, columns,
available extra attributes, timestamp format and offset. Review the mapping
and confirm storage again. Only the newest mapping of each log counts.
Columns are matched by name, and source event identity settings stay separate.

`POST /v1/event-logs/mapping-matches` accepts `columns` and `delimiter` for one
header, optional `other_headers` with columns for up to three other delimiters,
and optional `preferred_log_id`. Each match includes its
`missing_attribute_columns`.

### Preview a CSV mapping

Use **Preview CSV mapping** before confirming storage. OWG shows the accepted
and rejected rows in the sample, a few normalized events, and any timestamp
offset assumptions. The preview uses the import parser in isolation and
checks up to 512 KiB and 2,000 complete records. Source-ID checks apply within
that sample. The full import validates the entire file and checks existing
identities when appending.

An import or append runs one at a time. You can prepare another file while a
save is running. The completed save keeps newer file and mapping choices in
the form.

### SQLite database files

When switching files or tables, the import form keeps the latest selection.
If a table cannot be read, the last successfully converted table stays in the
form for review.

A `.sqlite`, `.sqlite3` or `.db` file chosen for import is read like an Excel workbook (below): its tables
take the place of worksheets (the first table by name is read first; pick another under
*Worksheet*), and the chosen table becomes CSV for the ordinary column mapping, consent and quality
report. `POST /v1/event-logs/sqlite-to-csv?table=...` with the file as the body does the same and
returns `sheets`, `sheet`, `rows`, `columns`, `csv` and `binary_cells_left_empty`. Nothing is stored:
the upload is copied to a temporary file in the data folder, read and removed.

The file must start with the SQLite header and is opened read-only and immutable, with the schema
treated as untrusted (`trusted_schema` off, `query_only` on, extensions disabled). Only real tables
are offered, not views, virtual tables or SQLite's own tables, and each is read with a plain
`SELECT *`. A read stops after 120 seconds, 2,000,000 rows or 500 columns, and the upload may be at
most 256 MB. NULL becomes an empty cell and binary values are left empty and counted. SQLite stores
no time zone, so the import asks which offset times without one use.

### JSON Lines files

A `.jsonl` or `.ndjson` file (one JSON object per line, as APIs and log shippers write) is converted
to CSV in the same way and then mapped like any CSV. Nested objects become dotted columns
(`customer.id`, up to five levels), lists and other non-scalar values are kept as compact JSON text,
`null` becomes an empty cell and booleans `true`/`false`; columns appear in the order first seen.
Blank lines are skipped; any other line that is not a JSON object refuses the file with its line
number. At most 2,000,000 lines, 500 columns and 1 MB per line are read, and the upload may be at
most 256 MB. `POST /v1/event-logs/jsonl-to-csv` with the file as the body returns `rows`, `columns`
and `csv`. Nothing is stored. A `.json` file is still read as OCEL 2.0 JSON.

### Parquet files

A `.parquet` file, as data warehouses and lakes export, is read with DuckDB's `read_parquet` in an
in-memory connection that cannot install or load extensions, converted to CSV and then mapped like
any CSV. The file must start and end with the Parquet magic bytes. Timestamps with a time zone become
ISO 8601 in UTC with `Z`; timestamps without one are written without an offset and the import asks
which offset they use; dates stay ISO dates, lists and structs become compact JSON, NULL an empty
cell, and binary values are left empty and counted. At most 2,000,000 rows and 500 columns are read,
and the upload may be at most 512 MB. `POST /v1/event-logs/parquet-to-csv` with the file as the body
returns `rows`, `columns`, `csv` and `dates_have_no_offset`. Nothing is stored: the upload is copied
to a temporary file in the data folder, read and removed.

### Excel workbooks

Choosing an `.xlsx` file in the dashboard converts its first worksheet to CSV
on this computer (a workbook with several sheets shows a "Worksheet" choice;
picking another sheet converts that one from the same workbook) (`POST /v1/event-logs/xlsx-to-csv`, with `?sheet=` for another
sheet). The response holds the CSV and is not stored. The dashboard then puts
the CSV in place of the workbook, and you map columns, consent and import as
usual. The first non-empty row is the header. Cells become text: strings as
written, TRUE/FALSE, numbers without a trailing `.0`, and cells formatted as
dates or times become ISO 8601 times to the millisecond (1900 and 1904 date
systems).

Excel stores no time zone, so when the sheet has date cells the dashboard
fills in your browser's current offset under "Offset for times without one".
Change it if the export came from another zone. A single fixed offset is
applied, so an export spanning a daylight-saving change is off by an hour on
one side: fix the source column or import the two periods separately.

Refused: files that are not a zip with `xl/workbook.xml` (including Excel
97-2003 `.xls`), macro-enabled workbooks, XML parts with a `DOCTYPE`, uploads
over 64 MB, parts over 256 MB or a workbook over 512 MB uncompressed, and
sheets over 2,000,000 rows or 500 columns. Formulas are read as their last
calculated value, as Excel saved it.

### Importing the same file twice

Each import and appended batch keeps the SHA-256 of the uploaded file in its quality report. When a
new import or append stores a file whose bytes match a file already loaded, into any saved log or an
earlier batch of the same log, the response lists those batches under `same_source_as` (`log_id`,
`name`, `batch`, `this_log`, `loaded_at`; at most 10) and the dashboard says "This exact file was
already loaded as ...". It is a notice only: the import still happens, and you can delete the copy
you do not need. The match is byte for byte, so a re-export with one changed row, a different line
ending or a renamed file with different content is not reported. Deleted logs are never named, and
the check reads only reports stored on this computer.

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

## Automatic deletion

An export is often needed for one project and not after it. **Automatic deletion** lets the owner choose a date after which the log is deleted from this computer, with everything derived from it, exactly as **Delete log** would. Saving the date asks for confirmation. **Keep the log** clears the date, and the date can be changed at any time.

Expired logs are deleted when the list of logs is read (the dashboard does this every time it loads), and when an agent lists or asks for a shared log. Agents are never answered for an expired log, even if they ask before anyone opens the dashboard; your own direct requests for it keep working until the next of those sweeps. Each deletion re-checks the date in the same locked transaction that deletes, so clearing or extending the date always wins over a sweep that had already picked the log. The date must have a UTC offset, be in the future and be at most 10 years ahead; in the dashboard it is the end of the chosen day, UTC.

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

## Storage used

Saved logs stay on this computer. **Storage used**, in the import card, shows:

- the size of the local database file, plus any write-ahead log not yet compacted;
- the free space on that disk;
- for each log, largest first: its events, objects, import date and estimated size and share.

DuckDB does not report bytes per log, so a log's size is estimated from the rows it owns (events, event-object links, objects and attribute values) as a share of the file. Deleting a log frees about that much once the database is compacted (`GET /v1/event-logs/storage`, dashboard session only).

## Sharing with AI agents

A log can be shared, read-only, with AI apps connected to OpenWorkGraph over MCP, at the
`aggregates` or `cases` level. Logs are not shared by default. See
[PROCESS_INTELLIGENCE_FOR_AGENTS.md](PROCESS_INTELLIGENCE_FOR_AGENTS.md).

**Show recent agent reads** in *Share with AI agents* lists what connected agents asked for. It shows each call that reached one of the log's tools: its time, the tool, and whether it was answered, refused (not shared at that level, not allowed, or input the tool rejected) or failed with an error. It also shows how often each tool was used in the last 30 days. Requests rejected before any tool ran, such as malformed JSON, are not listed. Only those facts are kept, never parameters or answers. The owner's own dashboard calls are not recorded. The latest 1,000 calls per log are kept, and they are deleted with the log (`GET /v1/event-logs/{log_id}/agent-access?limit=`, dashboard session only).

## API

All routes need the local dashboard session (`Authorization: OWG-Session ...`).
AI bearer tokens and MCP clients cannot read or change imported business logs.

| Route | Purpose |
| --- | --- |
| `GET /v1/event-logs` | List stored logs. |
| `POST /v1/event-logs/{log_id}/rename` (`{"name"}`) | Give a stored log a new name (1 to 120 characters, no `<`, `>` or control characters, as at import); its events and analyses are unchanged. In the dashboard: **Rename log**. |
| `POST /v1/event-logs/import?format=csv\|xes\|ocel\|ocelxml&name=...` | Import the request body. Requires `X-OWG-Local-Storage-Confirmed: true`. CSV also takes `case_column`, `activity_column`, `timestamp_column`, `resource_column`, `delimiter` (`t` for tab) and repeated `attribute_column` for up to 32 extra columns to keep as event attributes (nothing else is stored). |
| `POST /v1/event-logs/timestamp-formats?column=...&delimiter=...` | Suggest a timestamp format from the start of a CSV file (body, at most 256 KB): `candidates` (each with `pattern`, `order`, `has_offset` and how the first value reads), `ambiguous`, `suggested`. Nothing is stored. Imports and appends take `timestamp_format=`. |
| `POST /v1/event-logs/xlsx-to-csv?sheet=...` | Convert one worksheet of the `.xlsx` request body to CSV (`csv`, `sheet`, `sheets`, `rows`, `columns`, `date_columns`, `dates_have_no_offset`). Nothing is stored. |
| `POST /v1/event-logs/{log_id}/delete` | Delete the log and every row that belongs to it. |
| `GET`/`POST /v1/event-logs/{log_id}/expiry` (`{"delete_after": ISO time \| null}`) | Read, set or clear the date after which the log is deleted automatically (below). |
| `POST /v1/event-logs/{log_id}/export/matching-cases` | Download matching cases. Body contains `cohort` and optional `case_id`, `include_unfiltered`, `case_type`, `events_from` and `events_before`. Query `format` is `csv` (default), `csv-review`, `xes`, `xes-attributes`, `ocel`, `ocelxml`, `sourcecsv`, or `sourcecsv-review`. Source-ID formats require the `source_case` notion. |
| `POST /v1/event-logs/{log_id}/derive` | Save the cases matching a filter as a new log (below). |
| `POST /v1/event-logs/combine` | Combine 2 to 10 saved logs into a new log (below). |
| `GET /v1/event-logs/{log_id}/quality` | Stored quality and mapping reports per import and append, plus current whole-log diagnostics (below). |
| `GET /v1/event-logs/{log_id}/summary` | Events, cases, activities, time span and case-duration median/p90/mean. |
| `GET`/`POST /v1/event-logs/{log_id}/description` (`{"description"}`) | The owner's description of the log (at most 2,000 characters; an empty text removes it), shown and edited in **About this log**. It never changes the events and is deleted with the log. |
| `GET /v1/event-logs/{log_id}/object-types` | Object types, objects per type and shared events between types. |
| `GET /v1/event-logs/{log_id}/object-map` | Per-object-type lifecycle maps, links between object types and activities shared by several types. |
| `GET /v1/event-logs/{log_id}/object-relationships` | Supplied directed object assertions, with bounded type/qualifier groups. |
| `POST /v1/event-logs/{log_id}/object-relationships/query` | Literal endpoint/type/qualifier and incoming/outgoing filters, revision-bound keyset pages, current endpoint event counts. Does not merge cases. |
| `GET /v1/event-logs/{log_id}/object-flattening` | Per object type, what analysing it as the case leaves out (deficiency), copies (convergence) and turns into apparent repetition (divergence), plus qualifiers. |
| `POST /v1/event-logs/{log_id}/object-constraints` | Rules across object types: objects per event, related objects per object, and an activity only after a required activity of every related object. |
| `POST /v1/event-logs/{log_id}/object-process-model/discovery`, `…/conformance` | Object-centric process model: a lifecycle model per object type and single or variable arcs, discovered from the log or edited, with each object's lifecycle replayed and each event checked against its arcs. See [OBJECT_CENTRIC_PROCESS_MODELS.md](OBJECT_CENTRIC_PROCESS_MODELS.md). |
| `POST /v1/event-logs/{log_id}/object-process-model/preview` | Validate a model JSON definition and its object types against a destination log before using it for conformance. |
| `GET /v1/event-log-domain-packs`, `GET/POST/DELETE /v1/event-logs/{log_id}/domain-packs/…` | Versioned domain packs (order to cash): map the pack's roles to the log's names, install with version and parameters, run its metrics, rules and reference model as one report. See [DOMAIN_PACKS.md](DOMAIN_PACKS.md). |
| `GET /v1/event-logs/{log_id}/object-attributes` | Per object type and attribute, how timed values change and how many event links happened while another value than the stored one held; `object_id` adds one object's timeline. |
| `GET /v1/event-logs/{log_id}/variants` | Most frequent variants with share and median duration. |
| `GET /v1/event-logs/{log_id}/variants.csv` | Every variant (up to 10,000) as spreadsheet-safe CSV: rank, path (activities joined with ` > `), steps, cases, share, running share, median and 90th-percentile duration in seconds; honours the case filter. The dashboard's **Download all variants (CSV)** under *Most common variants* saves it; the `X-OWG-Variants` header says when the list was cut. |
| `GET /v1/event-logs/{log_id}/activities.csv` | Every activity of the selection (up to 50,000) as spreadsheet-safe CSV: events, cases, case share, events per case, cases with repeats, resources, share of cases it starts and ends, median and 90th-percentile wait before it in seconds, first and last time seen. Honours the case filter, an applied activity mapping and hidden activities. **Download activity statistics** under *Activity and resource statistics* saves it; the `X-OWG-Rows` header says when the list was cut. |
| `GET /v1/event-logs/{log_id}/resources.csv` | The same per resource (blank resources as `(no resource)`): events and event share, cases and case share, activities, most frequent activity, median wait before its events, first and last time seen. **Download resource statistics** saves it. |
| `GET /v1/event-logs/{log_id}/directly-follows` | Directly-follows graph with start and end activities. |
| `GET /v1/event-logs/{log_id}/activities` | Activity frequency and rework (cases that repeat the activity). |
| `POST /v1/event-logs/{log_id}/attribute-profile` | Coverage, type, level and frequent values of every event attribute (below). |
| `GET /v1/event-logs/{log_id}/kpis?sla_seconds=&automation_resources=` | Throughput p50/p90/p95, rework rate, SLA breach rate, automation rate and weekly completions. |
| `POST /v1/event-logs/{log_id}/benchmark` | Compare two case cohorts (JSON body, below). |
| `POST /v1/event-logs/{log_id}/costs` | Process cost from your cost model (below). |
| `GET`/`POST /v1/event-logs/{log_id}/cost-model`, `POST .../cost-model/delete` | The log's saved prices (below). |
| `POST /v1/event-logs/{log_id}/map-comparison` | Compare the process maps of two cohorts path by path (below). |
| `POST /v1/event-logs/comparison` | Compare two saved logs, or one log under two filters (below). |
| `POST /v1/event-logs/{log_id}/impact` | Before/after impact of an intervention on cycle time, rework, SLA breaches or an activity count, with bootstrap intervals, an optional control cohort and a placebo check. See [INTERVENTION_IMPACT.md](INTERVENTION_IMPACT.md). |
| `GET`/`POST /v1/event-logs/{log_id}/change-markers`, `POST .../change-markers/{marker_id}/delete` | The owner's dated notes on the timeline ("Automation went live"), picked again in *Intervention impact*. See [INTERVENTION_IMPACT.md](INTERVENTION_IMPACT.md). |
| `GET /v1/event-logs/{log_id}/simulation/calibration` | Arrival rate, routing, activity times and resources measured from the log. |
| `POST /v1/event-logs/{log_id}/simulate` | Run a what-if scenario against the measured baseline (JSON body, below). |
| `POST /v1/event-logs/{log_id}/conformance` | Align observed variants with a reference model (JSON body, below). |
| `POST /v1/event-logs/{log_id}/trace-clusters` | Group cases into clusters of similar behaviour (trace clustering, below). |
| `GET /v1/event-logs/{log_id}/model-discovery?noise=&activities=` | Discover a draft process model with decisions, loops and parallel steps for review in the process catalog (below). |
| `GET /v1/event-logs/{log_id}/waiting-time?lifecycle_attribute=&end_attribute=` | Processing and waiting time per activity from lifecycle or end-time attributes (below). |
| `GET /v1/event-logs/{log_id}/dotted-chart?time=absolute\|relative&order=start\|duration&max_cases=` | Events over time per case for a dotted chart (below). |
| `GET /v1/event-logs/{log_id}/drift?window=month\|week\|cases&window_cases=&min_cases=` | Change points in how cases run over time (below). |
| `GET /v1/event-logs/{log_id}/playback?activities=&max_cases=` | Per-case event offsets for animating cases along the process map (below). |
| `GET`/`POST /v1/event-logs/{log_id}/filter-sets`, `POST .../filter-sets/{set_id}/delete` | Saved, reusable case filters (below). |
| `GET`/`POST /v1/event-logs/{log_id}/case-tags`, `POST .../case-tags/remove` | Analyst tags on cases (below). |
| `GET`/`POST /v1/event-logs/{log_id}/activity-glossary`, `POST .../activity-glossary/delete`, `POST .../activity-glossary/import`, `GET .../activity-glossary.csv` | The owner's notes per activity (below). |
| `GET /v1/event-logs/{log_id}/root-cause?outcome=...` | Case features associated with an outcome, with confidence intervals. |
| `GET /v1/event-logs/{log_id}/export?format=csv\|xes\|ocel\|ocelxml` | Download the log. |
| `GET /v1/event-logs/{log_id}/ocel.sqlite` | Owner-only download of the retained OCEL 2.0 log as standard SQLite event/object and relationship tables; created temporarily and cleaned up after response. |
| `GET /v1/event-logs/{log_id}/report.html?sla_seconds=&variants=` | Download a self-contained HTML report (below). |
| `GET`/`POST /v1/event-logs/{log_id}/saved-reports`, `GET .../saved-reports/{name}` | Copies of the report saved on this computer (below). |

Analysis routes take an optional `case_type` for OCEL logs. Every response is
`Cache-Control: no-store` and is marked descriptive: it reports what the log
contains and makes no causal claims, verifies no business policy and
authorizes nothing.

## Shareable HTML report

**Download report**, next to **Export**, saves `<log_id>-report.html`, a single
file to attach to an email or ticket. It holds:

* the log's name, source and time range;
* tiles for cases, events, activities, variants, median and p90 case duration,
  the share of cases that repeat an activity, events per case and, with
  `sla_seconds`, the share of cases over the SLA;
* the most frequent variants (15 by default; `variants` takes 1–50), with their
  share of cases and median duration;
* **Where to look first**: the five highest-ranked automatic insights (see
  *Where to look first* below), using the report's case filter and SLA;
* activity frequencies;
* the slowest transitions: the median and 90th-percentile time between one
  activity and the next, for up to 10 transitions seen in at least 3 cases
  (and 1% of cases). This is elapsed time between recorded events, not
  measured work or verified waiting;
* cases completed per week.

The report applies the selected case notion, the applied activity mapping and
any case filter (`started_from`, `started_before`, `resource`, `activity`,
`attribute`, `follows`). When a filter is in effect, it states how many of the
log's cases it covers.

The file contains no scripts and loads nothing from the network, so it opens
the same offline. The response forbids scripts and external resources with a
Content-Security-Policy. Log names, activities and attribute values are
HTML-escaped. Every figure is descriptive and the report carries the same
caveat as the dashboard: durations are observed elapsed time, and repeated
activities are not verified rework. The route requires the local dashboard
session.

**Saved copies.** **Saved copies**, next to **Download report**, lists copies
of the reports saved on this computer, newest first, and lets you download
them. **Save a copy now** uses the selected case notion and active case filter.
The dashboard-only `POST /v1/event-logs/{log_id}/saved-reports` accepts a JSON
body with optional `case_type` and `cohort` fields. A request without a body
saves the whole log using its default case notion.

To save copies regularly, for example to compare this week's report with last
week's, add an **Event log report copy** job under *Scheduled jobs* with the
log's ID. Choose **Schedule the current case selection** in **Saved copies** to
retain the active case notion and filter for each run. Jobs created directly
from **Scheduled jobs** use the full log and its default case notion. Each
job is local and runs at least every 15 minutes, at most every 7
days. The newest 10 copies per log are kept in the data folder under
`event_log_reports/<log_id>/`. Deleting the log deletes its copies and its
report and signal schedules. Copies are never sent anywhere.

Select a reference and comparison copy to compare any two saved reports that
have figures. The newest pair appears when you open the panel.

**What changed.** Each copy is saved with its headline figures in a JSON file
next to the HTML. The panel identifies comparisons between different selections
and displays **Changes since the previous copy**:

* cases, events, activities and variants;
* median and 90th-percentile case time;
* events per case and the share of cases repeating an activity;
* the total cost, when both copies had saved prices in the same currency;
* activities that are new or no longer seen;
* whether the most common variant changed;
* the largest changes in cases per activity.

`GET /v1/event-logs/{log_id}/saved-report-changes` compares the two newest
copies, or the two given as `older` and `newer`. A change can come from
appended events, an applied activity mapping or edited prices as much as from
the process itself.

It opens with the same **In plain words** summary as the dashboard. The report also has a
**Model fit and adherence** section: every case is aligned with the process
model discovered from the same cases (noise 20%), giving the alignment fitness, the share of cases
that follow the model exactly, the steps most often skipped or done where the model does not allow
them, how the most common non-fitting paths are explained, and, when cases span several months,
fitness by the month cases started. The section is left out when the model cannot be computed.

## In plain words

**Summarize in plain words**, at the top of the results, writes a short summary of the selection: its
size and period, how long a typical case takes, the most common path, how many cases repeat
activities, how well cases follow the discovered model (and which step is most often skipped or out
of place), the top two automatic insights, and how many cases stand out. No language model is used:
each sentence is filled from a computed figure, and **Show** jumps to the analysis behind it.
`GET /v1/event-logs/{log_id}/story` plus the case-filter parameters.

## Finding an analysis

The results page has many analysis sections. **Find an analysis**, at the top of the results, lists
them all; typing filters the list by section name and description (for example "conformance",
"funnel" or "stand out"), and choosing one scrolls to it and moves keyboard focus to its heading.

## Where to look first (automatic insights)

Use **Review cases** to see which cases contribute to a transition, repeated activity,
detour or case-attribute finding. **Slowest cases** and **Save as log** for attribute
findings use the same selection. These actions combine the finding's case filter
with the case filter used to calculate the insight. The JSON cohort form accepts
`all_of` with two to four nonempty case filters. Each case must match every filter.


After **Analyse**, **Where to look first** lists up to six findings, ranked by the
case time they involve. The same list comes from
`POST /v1/event-logs/{log_id}/insights` with
`{"limit": 8, "sla_seconds": null, "cohort": {}, "case_type": null}`.

| Kind | Shown when |
| --- | --- |
| `slow_transition` | One step-to-step gap accounts for at least 15% of all recorded case time. |
| `repeated_activity` | An activity recurs within at least 5% of cases. It shows the median duration with and without the repeat. |
| `costly_detour` | Cases with a minority activity take at least 1.5× as long as the rest. |
| `attribute_segment` | Cases with a case-attribute value take at least 1.5× as long as the rest (median). The value can come from the import or a joined case table. Numeric attributes with more than 10 distinct values are grouped by this log's quartiles. At most the 3 largest are listed. The finding's `cohort` (`attribute` or `attribute_range`) selects exactly its cases. In the dashboard it offers "Slowest cases" and "Save as log". |
| `long_tail` | Always shown: the slowest tenth of cases and their share of case time. |
| `variant_spread` | Always shown: how many paths cover 80% of cases. |
| `slowing_down` | The newer half of cases (by start) has a median at least 1.25× the older half's. |
| `sla_breaches` | With `sla_seconds`: the cases over that duration. |

Each finding carries its numbers, the cases it touches, a caveat, and the case
filter that reproduces it:

* `cohort` for API use;
* `follows` rules for the dashboard's case filter, so **Filter to these
  cases** applies it to every analysis.

Logs with fewer than 5 cases get no findings. Insights are descriptive: they
show where recorded time and repetition concentrate, not why. A 300,000-event
log takes about half a second.

Agents with access to a shared log read the same findings with `get_process_insights`
(aggregates only).

## Analytics

The dashboard card **Large event logs** (Evidence tab) offers all of the
following after **Analyse**.

### Attribute profile

Select a coverage count or a frequent value to open the matching cases. You can also
review cases where the attribute is missing or changes value. Matching uses the
literal event-attribute key and value within the profile's case selection. Each
result shows the number of matching events alongside the full case's event count.

**Attributes** (and `POST /v1/event-logs/{log_id}/attribute-profile` with
`{"top_values": 10, "cohort": {}, "case_type": null}`) lists every event
attribute in the log, ordered by how many cases carry it. Each attribute
shows:

* its event and case coverage;
* its number of distinct values;
* whether every value is a number. CSV cells are text, and text that parses as
  a finite number counts as numeric;
* whether it is **case-level** (one value in every case that has it, like a
  region) or **event-level** (changes within at least one case, like an amount
  at a step);
* whether it is unique per event, like a ticket number.

Categorical attributes list their most frequent values (`top_values` 1–50) with
their share of cases and the median duration of cases with that value. Numeric
attributes get min, quartiles, max and mean.

Blank cells and JSON nulls count as missing. At most 100 attributes are
returned (`attributes_truncated` says when more exist). Durations per value are
descriptive: cases with a value can differ from the rest in many other ways.

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
case contains it), `attribute` (`name=value` on the case object) and
`event_attribute` (`name=value` on at least one event of the case, which is
where columns kept from a CSV import live; values are compared as stored text).
`event_attribute` is accepted wherever a case filter is: query parameters,
`cohort` bodies and saved filter sets. The
dashboard downloads the result as JSON or CSV; CSV cells that could be read as
spreadsheet formulas are prefixed with `'`.

### Breakdown table

**Breakdown table** shows case figures by one or two dimensions, like a pivot
table (a small process cube). Choose what goes in the rows and, optionally, in
the columns:

* a built-in dimension: the month or weekday the case started, its first or
  last activity, its duration band (under 1 hour, 1 to 24 hours, 1 to 7 days,
  7 to 30 days, 30 days or more) or whether it repeats an activity
  (`duration_band`, `rework`);
* a case tag (`tag:<name>`): tagged and not tagged, for example the cases
  tagged as not fitting a reference net;
* a case attribute (`attribute:<name>`, as the case filter reads it);
* an event attribute (`event_attribute:<name>`, the first non-empty value
  recorded in the case; columns kept from a CSV import are event attributes).

Every cell gives the number of cases, their share of the selection, the median
and 90th-percentile duration, events per case and the rework rate (cases that
repeat an activity). With **Share doing an activity**, it also gives the share
of cases that do the chosen activity. The table ends with an **All** column
and row, the totals for each value.

Select any populated cell or total to see the cases behind its figures, then open a case to
review its event history. For an activity share, the list shows whether each case did the
selected activity.

At most 30 row values and 12 column values are shown. For a time dimension
the latest values are kept; otherwise the most common. The rest are grouped
as `(other)`, and cases with no value as `(missing)`. Literal values with these names keep
their own groups. The report uses distinct labels for its buckets and totals when needed.
Cells with fewer than 5
cases are marked `*`, because one case moves their figures a lot. The table
honours the case filter.

`GET /v1/event-logs/{log_id}/breakdown?rows=…&columns=…&with_activity=…`
(plus `case_type` and the case-filter parameters) returns the cells and
totals. `GET /v1/event-logs/{log_id}/breakdown/dimensions` lists the
dimensions available in the log. Both need the dashboard session. A
difference between cells describes the recorded cases, not a cause.

For case evidence, `POST /v1/event-logs/{log_id}/breakdown/cases` with the same query parameters.
Send the report's `row_key` and `column_key` in the body. Omit either key for the corresponding
total and omit both for the overall selection. Pages use literal case-key order. The optional
`limit` is 1–100 (default 50). Send the returned `next_after` as `after` to continue.

**Download as CSV** saves the same table for a spreadsheet: one line per cell (row value, column
value and every figure), then the row totals, the column totals and the overall figures, labelled
`All`. A `fewer_than_5_cases` column marks the small cells, and cells that would start a formula
are prefixed with `'`. `GET /v1/event-logs/{log_id}/breakdown.csv` takes the same parameters.

### Process cost

Select **Review case costs** to see cases in descending order of modeled cost, or
select the case count for a variant to review that exact sequence. The breakdown
shows activity and elapsed-time charges, repeat charges already included in
activity cost, and unpriced executions. Open a case to inspect its recorded events.

The log records what happened, not what it cost. **Process cost** applies your
own cost model to the observed events. `POST /v1/event-logs/{log_id}/costs`
takes:

```json
{"currency": "EUR", "activity_costs": {"Check invoice": 12.5, "Approve": 4},
 "default_activity_cost": null, "hourly_open_case_cost": 0.8,
 "cohort": {}, "case_type": null, "limit": 20}
```

* `activity_costs`: the cost of one execution of each activity (up to 500
  activities; each from 0 to 1,000,000,000).
* `default_activity_cost`: optional; prices every activity not listed.
  Activities with no price are left out of the totals and listed in
  `unpriced_activities`; their cost is never guessed.
* `hourly_open_case_cost`: optional holding cost, charged for each case's time
  between its first and last event.

The response gives:

* the total, activity and holding cost;
* the cost of repeated executions (every execution of an activity after its
  first in the same case) and its share of the total;
* cost per case (mean, median, 90th percentile, maximum);
* cost per activity (unit price and where it came from, total, share and the
  cost of repeated executions);
* the costliest variants (`limit`, 1–200);
* cost per recorded resource.

`cohort` takes the same keys as other analyses, so the cost of any filtered set
of cases can be compared with the rest. Repeated executions are not verified
rework.

Open **Price another activity** to search for an activity outside the suggested
list or enter an exact label. The editor keeps prices for labels that have yet
to appear in the log. Use **Show priced activities only** to review overrides
without scrolling through the suggestions.

**Saved prices.** **Save prices** keeps the model (currency, activity prices,
default price and hourly cost) with the log. Next time, the dashboard fills the
prices in. **Load saved prices** restores the saved model to the editor. The HTML report, and its saved and scheduled copies, then include a
**Process cost** section, which shows:

* the total cost;
* the mean and median cost per case;
* the share spent on repeated executions;
* the ten costliest activities;
* which activities have no price.

The section uses the report's case filter. API:

* `GET /v1/event-logs/{log_id}/cost-model` returns `{"saved": null}` or the
  model and when it was saved;
* `POST` with `{"currency", "activity_costs", "default_activity_cost",
  "hourly_open_case_cost"}` replaces the model;
* `POST .../cost-model/delete` forgets it.

A log keeps one model. It is deleted with the log and not copied when matching
cases are saved as a new log.

**Reuse a model.** Open **Transfer cost model** and download the editor's
prices as JSON. In the destination log, choose the JSON file to validate its
currency and prices, then select **Load previewed prices** to fill the editor.
Select **Calculate cost** to try the model and **Save prices** to keep it with
the destination log. The file contains only the currency, activity labels,
and prices. Its envelope is
`{"format":"openworkgraph.event-log-cost-model.v1","model":{...}}`.
`POST /v1/event-logs/{log_id}/cost-model/preview` validates the file and
returns the normalized model without changing saved prices.

### Cohort benchmark

Select a count to open the cases behind either cohort's totals, rework, SLA breaches,
activities, or exact variants. The side-by-side process maps offer the same review for
adjacent paths. A case breaches the SLA when its recorded elapsed time is strictly
greater than the configured threshold.

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

### Trace clusters

When a map has too many variants to read, **Trace clusters** splits the cases into a
few groups that each read as one way of working, like trace clustering in Celonis,
Disco or ProM. Send a JSON body; every field is optional:

```json
{"k": null, "max_k": 8, "max_variants": 200, "max_activities": 40,
 "cohort": {"activity": "Refund"}, "case_type": "order"}
```

- **Features.** Each variant (the ordered activities of a case) becomes a vector
  of `log(1 + count)` per activity plus each directly-follows pair it contains,
  scaled to unit length. Only the `max_activities` most common activities count
  (2–60).
- **Clustering.** The `max_variants` most frequent variants (2–500) are clustered
  with case-weighted k-means. It starts from the most common variant and then the
  variant farthest from the chosen centres, so the same log always gives the same
  clusters. The remaining rare variants join their nearest cluster.
- **Number of clusters.** Give `k` (2–12), or leave it out and the best silhouette
  between 2 and `max_k` is used. Every value tried is reported. The silhouette counts
  each variant once, because weighting by cases would always favour one cluster per
  variant.
- **Each cluster** reports:
  - its cases, share and variants;
  - its five most common variants;
  - the activities more and less common in its cases than in the whole log, with
    both shares and the lift;
  - its median and mean throughput time and its events per case.
- **Inspect cases** opens a cluster's member list, including rare variants that joined
  their nearest cluster. Each row opens the recorded event history.
- **Using a cluster as a case filter.** When its paths fit the limits, a cluster includes a `cohort`,
  `{"label": "Cluster 1", "variants": [[...], ...]}`. The `variants` cohort key keeps
  cases whose exact activity sequence is one of those listed (up to 500 variants of
  up to 1,000 activities). It works wherever cohorts do: KPIs, benchmarks (compare
  a cluster with the rest of the log) and the other cohort-aware analyses.

`POST /v1/event-logs/{log_id}/trace-clusters/cases` accepts the same settings and the
selected cluster's `cluster_key`. It returns the total and a page of members ordered
by literal case key, with an optional `limit` of 1–100 (default 50). Send the returned
`next_after` as `after` to continue. If the cluster changes, rerun the report to get
its current key. This route requires a dashboard session.

From **Inspect cases**, **Tag cases in this cluster** recomputes the cluster in
the original case scope and can mark its current members, including clusters
whose paths cannot fit a variant filter. Preview the count and sample before
applying; existing tag notes on matching cases will be replaced. The preview is
checked again at save time, so changes to membership or existing notes require
another preview. The cluster is recomputed on the current log, not a frozen
report-time case list. Tagging is limited to 100,000 cases; inspect or download
larger case lists instead. Tagging does not change stored events and the cluster
report must be run again afterward. `POST /v1/event-logs/{log_id}/trace-clusters/tag/preview`
accepts the clustering settings, `cluster_key`, tag and note; apply with the
same settings and `expected_selection_sha256` at `/trace-clusters/tag`. Both
routes require a dashboard session. The server rechecks membership and existing
notes under the workspace lock and applies the tag transactionally.

**Download cluster report JSON** includes the original request, the complete
returned cluster summary, settings and case notion. The CSV gives one row per
cluster with the same coverage counts and the displayed top variants and
contrasting activities. These are the returned top five variants and up to six
contrasting activities per side, not a list of every variant or activity. Both
report downloads reflect the last completed analysis and are cleared when its
settings change. CSV text is escaped for spreadsheet review.

From **Inspect cases**, **Download cluster cases (CSV)** recomputes the selected
cluster with its original settings and scope, then downloads case keys, duration,
event count and first/last activity. Each file contains up to 50,000 cases in
case-key order. When more cases remain, download subsequent files using the next
page control; the dashboard reports progress. A continuation cursor is bound to
the log, cluster and settings. Formula-like text is escaped for spreadsheet review.
The cluster key detects a
change in the cluster's variant composition and counts since the report, but it
does not freeze case membership before the first download. For a multi-file
export, a streaming digest of all exported case rows is checked on every later
page; changed membership or exported values require starting again. Pages are
recomputed from the current log, and no case list is retained by this check.
This download requires a dashboard session.

Clusters describe the structure of the imported log. They are not process types,
causes, or a judgement of how any case was handled.

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
contains an activity, case ends with an activity, or **case has tag** (the cases
you or an alignment tagged, such as `off-model`; `outcome=tagged&outcome_tag=…`
in the API). Every case feature
(activity present, resource involved, case-object attribute value) that both
5+ cases have and 5+ cases lack is compared:

- outcome rate with and without the feature, and the risk difference;
- a 95% Newcombe hybrid-score interval for that difference;
- whether the interval still excludes zero after a Bonferroni correction for
  the number of features tested.

A numeric case attribute rarely repeats one value in 5 cases. Amounts,
quantities and scores therefore get a second test, when at least 90% of a
case attribute's values are numbers and it has more than 10 distinct values.
Its quartile ranges in this log (`amount < 460.75`, `460.75 ≤ amount < 821.5`,
...) are tested as features of kind `attribute_range`. The edges describe this
log; they are not thresholds anyone set.

An association carries `cohort` when an exact case filter can represent it:
`{"activity": ...}`, `{"resource": ...}`, `{"attribute": "k=v"}` or
`{"attribute_range": {"name": "amount", "min": 821.5, "max": 1182.25}}` (exact
edges). Labels use enough precision to distinguish nearby range boundaries.
No exact attribute filter is offered for shortened values or names containing
`=`, which the `k=v` filter syntax cannot represent. Any view that takes a
case filter can open the cases when a filter is present. Range filters are also accepted directly, as `attribute_range`
in cohorts and as a JSON query parameter
(`?attribute_range={"name":"amount","min":1000}`). `min` is inclusive, `max`
is exclusive, and either can be left out. Non-numeric and non-finite values
(including NaN and infinity) never contribute to the quartiles or match a range.
Range attribute names match literal stored keys, including punctuation and Unicode.

In the dashboard, the "Its cases" column offers three actions for each
association that has such a filter:

- **Slowest cases** lists its ten slowest cases. Choosing one explains it in
  "Explain a case".
- **Tag cases** tags all of its cases. The note records the association, and
  the tag then works as a filter everywhere.
- **Save as log** saves its cases, with their full histories, as a new log.
  The current log is unchanged.

All three use the association's exact filter, so they act on exactly the
cases it counted.

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

Use **Download scenario inputs** to keep the submitted settings as JSON. Under
**Rerun saved simulation inputs**, open that file or a simulation JSON report
with submitted inputs. Review or edit the JSON, then run it against the current
log and case notion shown above the file picker. The simulator recalibrates
from the current data. Explicit overrides stay in the submitted inputs, and
omitted values use the current calibration. The new report identifies the log
and case notion used for the run.

Use **Compare staffing alternatives** after loading measured values to compare
two to four proposals against the same calibration and seed. The table shows
median and 90th percentile cycle time, completions, and the activities with
the longest mean waits. The table withholds deltas when overloaded queues or
incomplete runs invalidate the comparison.

**Download comparison inputs** saves the submitted settings. **Download
comparison report** also includes the results, source log and case notion,
calibration digest, and reasons for withholding deltas. Open either file
under **Rerun a saved staffing comparison**. Review or edit the settings
and run the comparison explicitly. OWG recalibrates from the selected
log and case notion, so a different log or a later revision may yield
different results.

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
- **Limitation:** by default routing is first-order, so the next step depends only on the current
  activity. `"routing_context"` relaxes that (see below).
- **Routing context:** `"routing_context": "history"` makes the next step depend on the previous and
  the current activity, so rework that usually happens once is not repeated as a memoryless walk would
  repeat it. `"routing_context": "attribute:priority"` gives each simulated case a value of the case
  attribute `priority`, drawn from its observed mix (the 12 most common values, then `(other)` and
  `(none)`), and routes it with the shares observed for that value. The value comes from the case
  object's attribute (OCEL) or else from the case's earliest event that carries it (CSV attribute
  columns). A context row is used only when at least 20 calibrated cases passed through it; otherwise
  the walk uses the first-order row. Editing an activity's row in `"transitions"` drops its context
  rows, so the edit applies to every case. The calibration (`…/simulation/calibration?routing=transitions&routing_context=history`)
  returns `transitions.context`, and results describe the context rows used. Shares are associations
  in the log, not effects of the attribute. In the dashboard: **The next step depends on**.
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

#### Priority classes

A scenario can serve some cases first. For example:

```json
{"priorities": [{"name": "urgent", "share": 0.3, "rank": 0}, {"name": "standard", "share": 0.7, "rank": 1}]}
```

- **Classes:** 2 to 5 classes, with shares adding up to 1 and ranks from 0 to 9, of which at least two differ.
  Each simulated case belongs to one class, drawn from a random number of its own, so adding classes never
  changes any case's arrival or route.
- **Queueing:** every queue, including shared pools, serves the lowest rank first and first come, first served
  within a rank. Priority is non-preemptive: work in progress is never interrupted.
- **Baseline:** the measured baseline keeps one first-come, first-served queue.
- **Results:** a row per class with its share, rank, median and 90th-percentile cycle time and mean wait per
  step. The assumptions say that the scenario models priorities.
- **Check:** one server at load 0.8 with 30% urgent cases is compared with Cobham's non-preemptive formulas
  (urgent cases wait 0.84 h, the others 4.21 h). The overall mean wait stays at the first-come, first-served
  3.2 h, because reordering a work-conserving queue keeps it.
- **Dashboard:** the simulation form takes classes as `urgent 30% 0, standard 70% 1`.

#### Batch processing

A scenario can batch an activity, for example a payment run:

```json
{"activities": {"Pay": {"batch": {"size": 10, "max_wait_minutes": 1440}}}}
```

- **How a batch runs:** the activity's cases wait until `size` are waiting, or until the oldest has waited
  `max_wait_minutes`. A free resource then takes up to `size` of them, lowest priority rank first, and serves
  the whole batch in one activity time. All members move on together.
- **No maximum wait:** a batch that never fills holds its cases. They are counted in
  `cases_waiting_in_unfilled_batches` and per activity, and are not measured as finished.
- **Offered load:** counts the work of full batches, that is the activity time divided by the size.
- **Limits:** a batched activity cannot also be in a shared pool. The measured baseline is never batched.
- **Results:** add `batch` to the activity's row: settings, batches run, mean batch size and cases still waiting.
- **Checks:**
  - with a size of 5, 1 case an hour and near-instant service, the mean wait is the expected
    (K − 1) / (2λ) = 2 h;
  - with a size of 100 and a 60-minute limit, it is (60 + 30) / 2 = 45 min, and no case waits longer;
  - members reach the next activity together.
- **Dashboard:** the simulation form batches one activity, with the cases per batch and an optional longest
  wait in hours.

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

## Variant coverage

Page through ranked event sequences while keeping the coverage targets and cumulative shares for
the full case selection. **Inspect variant cases** opens complete case histories, including
variants whose displayed sequence is shortened.

`GET /v1/event-logs/{log_id}/variant-coverage` accepts `limit` (1–200), `offset`, and the case
filter. Use `page.next_offset` for the following page. Each row has a `variant_id` for its full
sequence. Pass it to the dashboard-only `POST /v1/event-logs/{log_id}/cases/query` with the same
case notion and cohort to keep the match exact.

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

## Case attributes from a separate table

Event exports rarely carry everything known about a case. Region, customer
segment, contract value or channel usually sit in a separate table keyed by
case ID. "Case attributes from a table" joins the columns you choose from such
a CSV onto the log's cases. They then work like OCEL object attributes: in case
filters (`attribute=Region=EU`), root-cause associations and attribute
profiles.

- Ids are matched exactly against the cases (for OCEL logs, the objects of the
  current case notion). Rows whose id is not a case are counted and ignored.
  Cases without a row are counted and keep their attributes.
- A table that lists an id more than once is refused, because it would be
  ambiguous.
- Empty cells add nothing. Values are kept as text, trimmed, and at most as
  long as other attribute values.
- A column that cases already have is refused unless you choose to overwrite.
- Every case keeps at most 32 attribute names, stored and timed, so the log
  still exports as OCEL.
- Events and the log's content digests do not change. Each upload is recorded
  with its columns, counts and the SHA-256 of the file. New uploads keep their
  prior cell values locally for undo. Undo restores overwritten values and
  removes new values only in the cases that this upload changed. If a newer
  upload changed any of those cells, undo that newer upload first. Older uploads
  without prior-value records cannot be undone. Deleting the log removes its
  upload history and prior values.

| Route | Purpose |
| --- | --- |
| `POST /v1/event-logs/{log_id}/case-attributes?id_column=...&column=...&delimiter=;&object_type=...&replace=true` | Join the CSV body. Requires `X-OWG-Local-Storage-Confirmed: true`. |
| `GET /v1/event-logs/{log_id}/case-attributes` | Recorded uploads. |
| `POST /v1/event-logs/{log_id}/case-attributes/remove` | `{"upload_id": "...", "columns": [...]}` (columns optional). |

Case enrichment also accepts Excel workbooks. Choose an `.xlsx` file and a
worksheet. OWG converts that sheet to CSV locally for column selection and the
case ID join. Switching worksheets clears column selection and storage
confirmation. Store Excel IDs as text when leading zeros or formatting are
part of the identifier.

### Download a case table

Use **Download a case table** to get one row per case in the current case notion.
The active case filter limits the rows. Without a filter the table also includes
objects that have no events. Enter up to 32 attribute names to create blank
columns, or select **Include stored values** to download their static values.
CSV represents these values as text. Timed object attribute history belongs in OCEL.

For a template you intend to join back, choose **CSV for re-import** and use
`case_id` as the ID column when uploading the edited file. Spreadsheet review
CSV projects formula-like text and changes the headers for review.

The dashboard-only `POST /v1/event-logs/{log_id}/case-table/export` accepts
`case_type`, `cohort`, `columns`, `include_values`, and `spreadsheet_review`.

## Case tags

**Preview tag selection** shows how many cases match, which already have the tag, and a sample
of their current notes. A previewed assignment checks the same membership and existing notes
before applying. Reopen the preview if they have changed. From **Show cases**, use **Edit note**
to correct or clear a note while keeping the original assignment timestamp.

`POST /v1/event-logs/{log_id}/case-tags/preview` takes the same selection as the add route.
Pass its `selection_sha256` as `expected_selection_sha256` when applying. `POST .../case-tags/note`
takes the tag, literal `case_id`, new `note`, `expected_note` and `expected_tagged_at` from the list.

While investigating, tag cases such as "checked", "root cause: missing PO" or
"exclude: test data", and come back to them. A tag is a label of 1–60
characters with an optional note (up to 500 characters). Tags are stored on this
computer next to the log. They are the analyst's judgement, not source data,
and the log's events are never changed.

* **Adding tags:** `POST /v1/event-logs/{log_id}/case-tags` with either:
  * `{"tag": "checked", "note": "…", "case_ids": ["c1", "c2"]}`, for up to
    1,000 IDs. IDs that are not cases of the log are counted in
    `unknown_case_ids` and skipped; or
  * `{"tag": "escalated", "cohort": {"activity": "Escalate"}}`, for up to
    100,000 matching cases.

  Tagging a case again replaces its note.
* **Removing tags:** `POST /v1/event-logs/{log_id}/case-tags/remove` with
  `{"tag": "checked", "case_ids": ["c1"]}`. Leave out `case_ids` to remove the
  tag everywhere.
* **Listing tags:** `GET /v1/event-logs/{log_id}/case-tags` lists the tags with
  their case counts. Add `?tag=checked` to also get the tagged cases and their
  notes. Use `limit` (1–1000) and `offset` to page through them. The `page`
  object includes `total` and `next_offset`, which is null on the last page.
  In the dashboard, use **Show cases** to review notes page by page. Select cases
  on the current page and use **Remove tag from selected cases** to remove their
  tag.
* **Filtering by tag:** the `tag` key of the case filter (`?tag=` on GET
  analyses, `"tag"` in `cohort` bodies, and **Tagged** in the dashboard)
  selects tagged cases in every analysis.

A log keeps at most 100 tags and 200,000 tagged cases. Deleting the log deletes
its tags. A log saved from matching cases does not copy them.

## Activity glossary

Activity names in an export are often terse ("ZF02 posted", "Chk 2"). The **Activity glossary** section keeps the owner's own documentation next to the log, as the knowledge models of process mining tools do. For each activity you can record:

* **Description:** what happens in the step.
* **Team:** who usually performs it.
* **System:** the application where it happens.
* **Automation:** *manual*, *partly automated*, *automated* or *automation candidate*.

The section shows how many activities are described and the share of events they cover. **Download glossary (CSV)** saves the glossary as a spreadsheet; cells that would start a formula are prefixed with `'`.

The notes are stored locally and never change the log's events. A note stays when its activity is no longer in the log, for example after re-importing, and is marked as no longer in the log. Deleting the log deletes its glossary.

* **Listing:** `GET /v1/event-logs/{log_id}/activity-glossary` lists every activity with its events, its notes and the coverage.
* **Saving an entry:** `POST /v1/event-logs/{log_id}/activity-glossary` with `{"activity", "description"?, "owner_team"?, "system"?, "automation"?}` saves one entry. Sending only the activity clears it.
* **Deleting an entry:** `POST .../activity-glossary/delete` with `{"activity"}` removes it.
* **Importing:** `POST .../activity-glossary/import` with CSV text (an `activity` column and any of `description`, `owner_team`, `system`, `automation`) fills the glossary in one go. The dashboard's **Import glossary (CSV)** does the same. A downloaded glossary imports back unchanged, and rows for activities not in the log are skipped and listed.
* **Agents:** when the log is shared with agents, they can read the glossary with `get_process_activity_glossary`. Activities seen in fewer than 5 distinct cases are left out.

## Explain a case

**Explain a case** offers the five slowest cases (honouring the case notion and
filter), or takes any case ID, and shows where that case's time went:

* each step-to-step gap next to the median gap of the same transition across
  the log;
* the steps that took longest above typical, with their share of the case's
  extra time;
* activities the case repeats, and steps that fewer than 10% of cases take;
* the case's duration against the log's median and 90th percentile.

A transition that fewer than 3 cases take has no typical gap, so all of its
time counts as above typical.

API:

* `POST /v1/event-logs/{log_id}/cases/explain` with `{"case_id": "…",
  "case_type": null}`;
* `POST /v1/event-logs/{log_id}/cases/slowest` with `{"limit": 10, "cohort":
  {}, "case_type": null}`.

Both need the dashboard session. Gaps are elapsed time between recorded
events, not measured work or verified waiting.

### Similar cases

**Find similar cases** (in *Explain a case*) lists the ten recorded cases
whose path is closest to the case entered. It answers "have we seen this
before, and how did it go?".

* **Distance** counts the steps that only one of the two paths has, after
  matching equal activities in order. This is the same alignment as *Compare
  with case*, so each row's **Compare** button lines the two cases up step by
  step.
* **Similarity** is `1 - distance / (events in both cases)`: 100% for the
  same path, 0% when no step is shared.
* Cases at the same distance are ordered by how close their duration is to the
  chosen case's.
* **Differences** list activities the similar case does more often (`+`) or
  less often (`−`) than the chosen case. "Same steps, other order" means only
  the order differs.

The summary gives how many cases follow exactly the same path, and the median
duration of the cases listed against the chosen case's own duration. The
search honours the case filter, but the chosen case itself is found even if
the filter leaves it out. Each distinct path is compared once. Paths that
cannot be closer than the ones already found are skipped, and at most 20,000
paths are compared. Cases with more than 500 events are not compared.

`GET /v1/event-logs/{log_id}/similar-cases?case_id=…&limit=10` (1 to 50, plus
`case_type` and the case-filter parameters) needs the dashboard session.
Similar recorded paths do not mean the cases had the same cause or outcome.

## Case notes

**Case notes** (under Analyse) keep what you found about single cases next to them: why an order
waited three weeks, what the customer said on the phone. A note names a case or object ID of the
log and holds up to 2,000 characters; it records when it was written. Notes are your own text, not
events of the log: they never change its data or any analysis, stay on this computer, are never
shared with AI agents and are deleted with the log in the same transaction. A log keeps at most
5,000 notes.

Open **Case notes** inside a case's event history to read or add notes right there. The main
**Case notes** section searches note text and case keys and opens event histories in the
object type recorded for each note. The two views refresh after a note is added or deleted.

| Route | Purpose |
|---|---|
| `GET /v1/event-logs/{log_id}/case-notes` | Notes, newest first (at most 500 listed); `?case_id=` for one case |
| `POST /v1/event-logs/{log_id}/case-notes/query` | Dashboard-only search with optional `search`, exact `case_id`, and `limit` (1–500, default 30). Use the returned `next_after` as `after` for the next page. |
| `POST /v1/event-logs/{log_id}/case-notes` | `{"case_id": ..., "text": ...}` adds a note |
| `POST /v1/event-logs/{log_id}/case-notes/{note_id}/delete` | Delete a note |

## Organizational mining (handovers)

`GET /v1/event-logs/{log_id}/organization` (dashboard session only) describes how work moves
between the resources named in the log's resource column:

- `handover_of_work`: consecutive events in a case performed by different resources, with
  handover and case counts;
- `subcontracting`: work that passes from one resource to another and straight back (one
  resource's event, then another's, then the first one's again), as in PM4Py's subcontracting
  metric, with occurrence and case counts;
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

With resource names shown, select a case count for a named handover, return handover,
or shared pair. Select a supplied case key to open the recorded events. Each case
contributes once to an edge's case count, including when several resources in the
case fall into the grouped bucket. Occurrence counts still include every match.

The dashboard-only `POST /v1/event-logs/{log_id}/organization/cases` accepts
`names: "shown"`, two literal `resources`, a `kind` of `handover`, `return` or
`shared`, and the report's `cohort`. Use the response's `next_after` as the next
request's `after` to page through matching case keys.

No per-person duration, waiting time, speed, ranking or performance score is calculated,
not even on handover edges. Handover counts say who touched a case next, not who was
responsible or how well anyone worked. One million events are analysed in seconds
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_organization.py`).

### Roles

"Discover roles" groups resources that perform a similar mix of activities. This is organizational
role mining, as in Song and van der Aalst (2008), PM4Py's role discovery and ProM's organizational
miner.

1. Each resource's activity counts are compared with the Pearson correlation.
2. Starting from one group per resource, the two groups whose summed profiles correlate most are
   merged.
3. Merging repeats while the correlation is at least the threshold (default 0.65, as in PM4Py;
   `threshold` must be above 0 and below 1).

Each role lists:
- its members;
- its events;
- its activity mix (the top eight activities with their shares);
- the activities that only this role performs.

The 200 most active resources and the 200 most frequent activities are considered.
The view reports the number of events with resource labels, resources and
activities represented out of the available totals. An activity is listed as
exclusive only when every recorded resource performing it in the selected cases
belongs to that role, including checks against resources excluded by the limit.
Adjust **Role similarity threshold** and rerun to compare groupings. Resources are
pseudonymous ("Resource 01", numbered by event count) unless names are asked for. Roles describe
recorded work, not job titles, responsibilities or how well anyone worked. No duration, speed,
ranking or score of any person is computed.

`GET /v1/event-logs/{log_id}/roles?threshold=&names=pseudonymous|shown&case_type=` plus the
case-filter parameters. Dashboard session only.

## Signals

Signals are saved conditions you check a log against. This is local, signal-only parity with
Celonis Action Engine and UiPath process alerts. Rules belong to one log, are immutable
(delete and add again to change one), are limited to 50 per log, and are deleted with the log.

- `GET /v1/event-logs/{log_id}/signals` lists the rules.
- `POST /v1/event-logs/{log_id}/signals` adds one, for example:
  `{"name":"Left open","when":{"kind":"cases","condition":"open_longer_than","seconds":172800,"end_activities":["Close"]}}`
- `POST /v1/event-logs/{log_id}/signals/preview` checks one draft with the same request body.
  It returns whether the draft would trigger, the measured value or matching cases, and the
  case population checked. The draft preview leaves saved signals and evaluation history alone.
- `POST /v1/event-logs/{log_id}/signals/{rule_id}/delete` removes one.
- `POST /v1/event-logs/{log_id}/signals/evaluate` checks every rule now.

Use **Preview signal** to check the current draft before choosing **Save signal**.
When the analysis has a case filter, choose **Current case filter** to save its
conditions with the rule. Signal conditions and their case filters use the
original recorded activities. **All cases** is selected by default. Later changes to
the analysis filter leave saved rules unchanged. For case conditions, enter
**Minimum matching cases** to set the trigger count.

Use **Move signal rules between logs** to download rule definitions as JSON, or
select a JSON file and preview the import. The file includes each rule’s case notion,
case filter, and condition. The destination must have the corresponding case notion.
The import skips duplicate definitions and saves all remaining rules together if
they fit. Screenshots, event data, prior results, acknowledgements, schedules and
alerts are separate from the rule definitions. A changed rule set needs fresh
approval for any existing pinned signal schedule.

`GET /v1/event-logs/{log_id}/signals/pack` downloads a
`openworkgraph.event-log-signal-pack.v1` document. POST the document to
`/signals/pack/preview` or `/signals/pack/import` under the destination log.
Files can hold up to 50 rules and 4 MiB.

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

The builder can use all cases or the current case filter. Saving the result
captures the case notion and filter. Running it later uses that saved scope,
and loading it for editing keeps the saved filter available in **Cases to
analyse**.

Use **Download definition** beside a saved analysis to export its specification,
including inline metrics and the case notion. In another log, select the JSON
file under **Import an analysis definition**, review the scope, and save it.
The destination must have the same case notion. Import skips an identical
saved definition and leaves the analysis ready to run.

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

For a case-level result, select **Review cases** on a result row to see its
matching cases, their durations, event counts, and any defined metric values.
The case list uses the original filters, dimensions, and case notion. Open a
case to inspect its retained events. If the log, applied activity mapping,
hidden activities, or case tags have changed, rerun the analysis before
opening the group.

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

## Saving matching cases as a new log

Use **Download matching cases** to download the original events directly.
Filters use the selected case notion and the applied activity mapping. The export
retains original activity names and includes events collapsed by the mapping.
Choose exact CSV for interchange, spreadsheet review CSV for formula-like cells,
or XES for one trace per selected case. **XES with retained attributes** includes
static trace and event values. The XES projection follows the chosen case notion.
Choose **OCEL 2.0 JSON** or **OCEL 2.0 XML** to include the objects linked to
the selected events, their timed attributes, and supplied relationships between
those objects. Set a case filter, a time window, or both. With a time window, the download retains events
at or after the start and strictly before the end. Either bound can be left blank.

Choose **Preview copy** to see how many cases, events, linked objects and
supplied object relationships the current selection would retain. Saving a
copy recalculates the selection against the source log.

**Save or export matching cases → Save as new log** (after **Analyse**), or
`POST /v1/event-logs/{log_id}/derive` with
`{"name": "Escalated orders", "cohort": {"activity": "Escalate"}, "case_type": null}`,
copies the cases that match a case filter into a new saved log. `cohort` takes
the same keys as other analyses and must filter something. It is evaluated
under the chosen case notion and the source's activity mapping.

* **What is copied:** every event of each matching case, with its attributes,
  so a case keeps its full history. The objects those events are linked to are
  copied too, with their links and timed attribute values. In an
  object-centric log, an event linked to a matching case also brings its other
  objects, which keep only the copied events. A supplied object relationship is
  copied when both endpoints are retained. It keeps its direction and qualifier.
* **What is not copied:** activity mapping, saved filters and analyses,
  signals, domain packs, source-identity keys and quality reports.
* **The source log:** unchanged.
* **Time window (optional):** `events_from` and/or `events_before` (times with
  a UTC offset; the dashboard's *Only events from / before* fields) trim the
  copy to the events inside the window, the "trim to timeframe" of other
  process-mining tools. A matching case keeps only its events in the window and
  is left out if it has none. With a window the case filter may be empty, so
  `{"name": "Q1", "events_from": "2024-01-01T00:00:00Z", "events_before":
  "2024-04-01T00:00:00Z"}` saves the whole log's first quarter. The response's
  `time_window` reports the cases with events in it and the events left out.
* **The new log:** counts against the 50-log limit. It can be analysed,
  exported, compared with the source (see *Comparing two logs*) or deleted like
  any other log.

### Combining saved logs

The same process is often exported in pieces: one file per region or per year, or one system each
for the order and invoice steps. **Combine with other saved logs** (under Analyse) copies the open
log and the logs you tick, 2 to 10 in all, into a new saved log; `POST /v1/event-logs/combine` with
`{"name": ..., "log_ids": [...], "cases": "shared" | "separate"}` does the same. The sources are
unchanged.

- **Shared** (default): an ID found in several logs is one case or object, with the events of all of
  them. Use it to join systems that use the same case key. Its type and attributes come from the
  first log listed that has it; the response counts `ids_in_several_sources` and
  `object_type_conflicts`.
- **Separate**: every log keeps its own cases. Object IDs are prefixed with the source number
  (`s1:c7`), so equal IDs in two regional exports never merge.

Event IDs are always prefixed (`s1:`, `s2:`, ... in the order listed, given per source as
`event_id_prefix`), and every event gets a `source_log` attribute naming its log, so you can filter
or break down by source. An existing `source_log` attribute is replaced and counted
(`source_attribute_replaced_on_events`). The combined log keeps the sources' format if they share
one, otherwise it is recorded as OCEL. As with a derived log, activity mappings, saved filters and
analyses, signals, domain packs, source-identity keys, object-to-object relationships, quality
reports, case tags and cost models are not copied.

### Anonymized export

**Anonymized export** (after **Analyse**) downloads the cases as a CSV meant
to leave this computer, for example for a consultant or a benchmark, with
less that points back to people. It honours the case filter.

* **Rare paths are left out.** A case is kept only when at least the chosen
  number of cases (2 to 1000, default 5) follow exactly the same sequence of
  activities. Activities that only occur on rare paths disappear with them.
* **Case IDs are replaced** by `C000001`, `C000002`, … in a random order, so
  the file cannot be joined back to the source system by ID or row order.
* **Time is coarsened.** *Relative* (default) moves each case to start at
  midnight on the first day of the month it started and keeps the gaps between
  its events; *day* keeps only each event's date (the `step` column keeps the
  order); *exact* keeps timestamps. *Synthetic start* moves every case to the
  same date while preserving elapsed time between events. *Event order only*
  assigns synthetic timestamps one second apart, so elapsed times are omitted.
* **Activity names** are kept by default. Choose activity codes to replace
  names consistently within the file. Each export assigns its own codes.
* **Resources** are left out (default) or replaced by `Resource 01`, … in a
  random order.
* **Attributes are never exported.** Columns: `case_id`, `step`, `activity`,
  `timestamp` and, if chosen, `resource`. Cells that would start a spreadsheet
  formula are prefixed with `'`.

**Check what is kept** shows the counts first; nothing is stored.
`GET /v1/event-logs/{log_id}/anonymized-export` returns that summary and
`GET /v1/event-logs/{log_id}/anonymized-export.csv` the file (header
`X-OWG-Anonymized`), both with `min_cases`, `time_mode`
(`relative|day|exact|elapsed|sequence`), `resource_mode` (`drop|pseudonymize`),
`activity_mode` (`keep|pseudonymize`), `case_type` and the case-filter parameters. At most 1,000,000 events are exported at once.
This lowers the chance that a case can be singled out; it is not a guarantee.
Durations, dates and activity names can still identify a case to someone who
knows it, so review the file before sharing it. For a map with formal
guarantees, see *Private process map (differential privacy)*.

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


### Folder feeds

For a saved feed, use **Preview files** to check matching files before a run.
The preview shows up to 40 of the oldest matching files and reads up to 32 MiB
to compare content with the feed’s append and failure history. It shows new,
already appended, previously failed, repeated, settling, and unchecked files.
A run rechecks the current folder and validates the mapped event rows.
Use **Append this file** for one new file in the preview. OWG checks the same
filename and content before appending. If the file changes, preview it again.

Many systems can only drop an export file into a folder every night or hour.
A folder feed points a saved log at such a folder. Each run appends every new
file that matches the pattern, using the column mapping of the log's latest
CSV import or append (timestamp format and offset included). The log, its
signals and its scheduled reports then stay current without re-importing by
hand.

- You create a feed for one absolute folder and one file pattern
  (`orders-*.csv`; `.csv`, `.tsv` or `.txt`), and you confirm that the folder
  may be read. Folder paths and filename patterns preserve spaces and Unicode
  literally; wildcard characters in the pattern retain their usual meaning.
  Path separators and control characters are not permitted in the pattern,
  and subfolders are not read.
- The feed starts with a copy of the CSV mapping. Use **Review mapping** to
  compare it with the latest manual CSV import or append, then **Use latest
  mapping** to update the existing feed. The new settings apply to future
  manual and scheduled runs. A run in progress stops before its next append
  if the mapping changes. Existing file history and events stay with the feed.
  Mappings with supplied source-event IDs are ineligible for this update.
- A file is appended once, identified by the SHA-256 of its content. A renamed
  copy is not appended again; an edited file counts as new.
- Files modified in the last minute are left for the next run, in case the
  exporter is still writing them.
- Each settled file is copied into a temporary snapshot while hashing. The
  snapshot is what gets appended; files whose identity, size or modification
  time changes during the copy are deferred. A successful append and its content receipt commit together;
  overlapping runs in the same OWG process are serialized at append time.
  Temporary storage must have space for the
  file, and the normal event-log import size limit applies.
- Events already in the log are skipped by the usual append rules, so
  overlapping exports are safe.
- A file that cannot be appended (wrong columns, for example) is recorded with
  the reason and is not automatically retried with the same content. Files too large to
  snapshot are reported on each run until removed or replaced, since no
  content hash is recorded. If an append commits but its later quality report
  fails, the feed records the append and surfaces a warning; it does not replay
  the file.
- **Retry failed files** explicitly retries unchanged content that has a
  failed receipt. It does not process new or changed files; use **Run now**
  for those. A successful retry replaces the failure receipt atomically, and
  another failed attempt updates its error. Missing or unsettled files wait
  until they are present and settled. Automatic runs do not retry failures and
  report them separately from files already appended.
- **Review failures** lists outstanding failures in pages of 50, including
  older failures outside the feed's latest-20 summary, and lets you retry one
  content hash at a time. A renamed file still matches; a changed, missing or
  unsettled file cannot be retried under its old hash. Pages can change while
  other runs or retries occur. Listing is bounded, but finding the selected
  content still scans and hashes matching settled files in the folder.
- Each run appends at most 20 files. Files in the folder are never changed or
  deleted.
- "Run every day" schedules the feed on the local scheduler (job kind
  `event_log_folder_feed`). Deleting the feed or the log removes the feed, its
  file records and its schedule. A scheduled run is labelled partial when
  files are not ready or additional matching files still need checking; it
  remains enabled and checks again at its normal interval. Partial status is
  not evidence that the folder or the source system is fully up to date.

| Route | Purpose |
| --- | --- |
| `POST /v1/event-logs/{log_id}/feeds` | `{"folder": "/abs/path", "pattern": "*.csv", "confirm_folder_reads": true}` |
| `GET /v1/event-logs/{log_id}/feeds` | Feeds with counts and the 20 latest file results. |
| `POST /v1/event-logs/feeds/{feed_id}/retry-failed` | Retry only previously failed, unchanged settled content; owner session and local-storage confirmation required. |
| `GET /v1/event-logs/feeds/{feed_id}/failures?after=...` | Page up to 50 outstanding failed content hashes. |
| `POST /v1/event-logs/feeds/{feed_id}/retry-failed/{sha256}` | Retry one outstanding hash; owner session and local-storage confirmation required. |
| `GET /v1/event-logs/feeds/{feed_id}/mapping` | Review the current feed mapping and the latest manual CSV mapping. |
| `POST /v1/event-logs/feeds/{feed_id}/mapping` | Apply the reviewed mapping by its `mapping_revision` and `mapping_sha256`. Requires local-storage confirmation. |
| `GET /v1/event-logs/feeds/{feed_id}/preview` | Inspect matching files and return the content hash for each eligible new file. |
| `POST /v1/event-logs/feeds/{feed_id}/append-file` | Append one verified file by `name` and `sha256`. Requires `X-OWG-Local-Storage-Confirmed: true`. |
| `POST /v1/event-logs/feeds/{feed_id}/run` | Append new files now. Requires `X-OWG-Local-Storage-Confirmed: true`. |
| `POST /v1/event-logs/feeds/{feed_id}/delete` | Delete the feed and its schedule. Appended events stay. |

## Compliance rules

`POST /v1/event-logs/{log_id}/compliance` (dashboard session only) checks cases against up to 20
declarative rules. These are Declare-style templates, as in ProM Declare, ABBYY Timeline or
Signavio process adherence:

| Template | Holds when | Applies to |
|---|---|---|
| `response` (`a`, `b`) | every A is eventually followed by B | cases containing A |
| `chain_response` (`a`, `b`) | every A is immediately followed by B | cases containing A |
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

Choose **Review violations** to page through the case keys and reasons for a rule, then
**Open case** to inspect its event history. The dashboard-only endpoint
`POST /v1/event-logs/{log_id}/compliance/violations` takes a single `rule`, the same optional
`case_type` and `cohort` as the check, and `limit` from 1 to 100 (default 50). For the next
page, send the returned `next_after` as `after`. Each page evaluates the current log.

Six rules over one million events take about 7 seconds locally
(`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_compliance.py`). These checks describe the
imported log against rules you entered. They are not verified policy, audit evidence or legal
compliance, and a violation may reflect missing events.

### Suggesting rules from a log

`POST /v1/event-logs/{log_id}/compliance/discover` (dashboard session only) proposes the rules
most cases of this log already follow, as ProM's Declare Miner does. It covers the `existence`
(in every case, or at most once), `response`, `precedence` and `not_coexistence` templates.

```json
{"min_confidence": 0.95, "min_support": 0.1, "max_activities": 25, "max_candidates": 50,
 "templates": ["existence", "response", "precedence", "not_coexistence"], "cohort": {}}
```

Only the `max_activities` most frequent activities are paired; the response says when others
were left out. Each candidate gives:

- the rule in the checker's own format;
- its *support*: the share of cases the rule applies to;
- its *confidence*: the share of those cases that follow it, with a Wilson 95% interval;
- its violating cases.

Candidates are ranked by the lower confidence bound, then by support. Their counts match exactly
what `compliance` reports for the same rule, and a test checks this. The response also carries
`rule_set`, the top 20 candidates ready to post to `compliance` or save one by one as signals.
In the dashboard, "Suggest rules from this log" lists the candidates, and "Add" puts one into
the rule list to check.

Suggestions are regularities of the imported log, not policy: a rule that 98% of cases follow
may be a habit, a system constraint or a gap in the data. Review each before relying on it.

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
object type. Choose **Inspect violations** to page through the events, objects or event–object
pairs for a completed rule. An object with retained events links to its event history.
100,000 OCEL events with 20,000 orders take under a second for three rules. Results
describe recorded links, not verified business relationships, audit evidence or legal compliance.

`POST /v1/event-logs/{log_id}/object-constraints/violations` takes the report's exact `rule` and
optionally its `log_revision` as `expected_revision`. For a supplied relationship rule,
`expected_graph` can also pin the report's `relationships_sha256` and `object_index_sha256`.
Keep these settings for subsequent pages and send the returned `next_after` as `after`.
The optional `limit` is 1–100 (default 50).

Use **Save as new rule set** to keep the current rules with the log. **Load rule set** restores
their definitions for review, and **Check object rules** evaluates them against the current log.
**Update loaded rule set** saves edits to the loaded revision. Deleting a saved set leaves the
current draft available. Deleting its log also deletes its saved sets.

Choose a second retained object log and select **Compare object rules** to evaluate the same draft
against both whole logs. The comparison shows violating and activated counts, the violation rate,
and the percentage-point change from the baseline. A rate change requires a complete result and
at least one activated unit in each log. Counts describe each retained population. Open violations
on either side to review its own events and objects. JSON and CSV downloads include both log IDs,
append revisions and applicable supplied graph digests.

`POST /v1/event-logs/{log_id}/object-constraints/compare` takes `rules` and `other_log_id`.
The response contains the individual `baseline` and `comparison` reports and
`violation_rate_changes` in rule order as fractions of one, with `null` for an unavailable change.
Use the corresponding report's revision and graph pins when paging through its violations.

The dashboard APIs are `GET/POST /v1/event-logs/{log_id}/object-rule-sets` and
`GET/PUT/DELETE /v1/event-logs/{log_id}/object-rule-sets/{set_id}`. Send `name` and `rules`
when creating or updating. Updates take `expected_revision` in the body, and deletes take the
same field as a query parameter.

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


### Petri net export (PNML)

"Download Petri net (PNML)" exports the discovered model as a workflow net in PNML
(ISO/IEC 15909-2). ProM, PM4Py, Apromore, WoPeD and most academic process-mining tools read this
format. The process tree is translated block by block:
- an activity becomes a visible transition, and a skip becomes a silent transition;
- a sequence chains its parts through new places;
- a choice puts every branch between the same two places;
- a parallel block adds a silent split and a silent join;
- a loop runs its body, then leaves through a silent transition or runs the redo part and the body
  again.

The net has one source place, marked with one token, and one sink place. The sink is declared in
`finalmarkings`, as PM4Py and ProM read it. Silent transitions carry the ProM `$invisible$` flag.

Place and transition identifiers are generated (`p0`, `t0`, …); activity names appear only as
transition names. Tests replay every trace up to six steps and check that the net accepts exactly
the traces the process tree accepts.

`GET /v1/event-logs/{log_id}/model-discovery.pnml?noise=&activities=&case_type=` plus the
case-filter parameters. It returns `application/xml` with an `X-OWG-Petri-Net` header that counts
places, transitions and silent transitions. Dashboard session only. Like the discovered draft, the
net is a candidate model for review, not a verified workflow.

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

**Download map (SVG)** and **(PNG)** save the map exactly as drawn at the current slider
settings, for slides and documents. The SVG is self-contained: styles are inlined, and
interactive attributes are removed. Its `<desc>` and a two-line footer record the log, the
slider settings, the coverage shown and that the map is derived from the supplied log, not a
verified business workflow. The PNG is rendered in the browser at twice the drawn size. **Download map data (CSV)** saves the numbers behind the same map
for a spreadsheet: one row per activity (events and cases), start activity, path (transitions,
cases and the median, mean and 90th-percentile gap in seconds) and end activity, with cells that
would start a formula prefixed with `'`.
Nothing is uploaded; activity names in the file are as sensitive as the log.

## Predicting open cases

`POST /v1/event-logs/{log_id}/predictions` (dashboard session only) estimates remaining time and,
optionally, an outcome risk for open cases. This is the local equivalent of ABBYY Timeline, IBM
Process Mining or Celonis predictive monitoring.

Enter an **Exact case key** to look up an open case, including one outside the preview. Both
live prediction views respect the active case filter and offer **Open case** to show recorded
event history. In either dashboard-only API, pass the optional literal `case_id` in the JSON
body. It restricts the returned prediction rows while keeping the comparison population and
backtest based on the selected cohort. Lookups report `case_selection` as `predicted`, `completed`,
`unsupported`, or `not_in_selection`.

Use **Rank estimates by** to prioritize open time, time since the latest recorded event, estimated
remaining time or estimated likelihood of the selected outcome. **Maximum estimates shown** controls
the dashboard shortlist (25–200). JSON and CSV download the displayed estimates and their coverage.
The JSON also includes the selected case filter, estimator settings and backtest.

The optional `rank_by` query parameter accepts `automatic`, `age`, `idle`, `remaining` or `outcome`.
Automatic ranks by estimated outcome likelihood when an outcome is supplied and estimated remaining
time otherwise. `ranked_by` reports the effective choice. Ranking does not change the compared cases,
their estimates or the backtest. `outcome` requires an outcome in the request body.

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

`POST /v1/event-logs/{log_id}/predictions/monitoring` replays the backtest over rolling windows to show whether
prediction quality holds over time. It reports error, range coverage and the Brier score per window, flags drift
in the latest window, and proposes (never applies) a recalibration. See
[PREDICTION_MONITORING.md](PREDICTION_MONITORING.md).

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

## Next activity in open cases

**Next activity in open cases**, or `POST /v1/event-logs/{log_id}/next-activity`
with `{"end_activities": ["Pay", "Close"]}`, estimates what usually comes next
in each open case. A case is open when its latest activity is not one of
`end_activities`.

**How it predicts.** It learns from completed cases how often each activity,
or the end of the case, followed the same last `history` activities (1–4,
default 2). When fewer than `min_support` completed-case steps (default 5) were
in that state, it backs off to a shorter history and finally to all
transitions. Each prediction says which history it used and how many steps
support it.

Use **Inspect training observations** next to an estimate to see the completed-case steps
that matched its history and what happened next. A completed case can contribute several
steps. A terminal step records a case ending. Open any listed case to review its event history.

The dashboard prediction response includes `matched_history` per case and an
`evidence_snapshot`. To use `POST /v1/event-logs/{log_id}/next-activity/training-evidence`,
send the original `settings` without `case_id`, the predicted `case_id`, and the response
digest as `expected_snapshot`. The optional page `limit` is 1–100, default 50. Pass
`next_after` as `after` to continue. Run the prediction again if the selected histories change.

Optional settings:

* `top`: the number of next steps returned per case (1–10, default 3);
* `limit`: the number of open cases returned, most recently active first
  (1–500, default 50);
* `case_id`: the exact case key to return, including cases outside the default preview;
* `backtest_share`: the share of completed cases used for the backtest
  (default 0.3);
* `cohort` and `case_type`: as for other analyses.

`expected_next_across_open_cases` sums the probabilities over all open cases,
giving an estimate of how many cases each step will come up for next.

**Backtest.** The model learns from the completed cases that ended before a
cutoff. It then predicts every step of the cases that ended after it,
including their ending. The backtest reports top-1 and top-3 accuracy against
two baselines: always guessing the most frequent next step, and the most
frequent successor of the current activity alone. With too few cases on either
side it says it is unavailable.

The predictions describe this log's history. They are not instructions and say
nothing about who should act.

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

In **Where cases wait**, choose **Inspect cases** to page through a hotspot's contributing cases
and open their event histories. The list shows the number of matching instances, total elapsed
waiting, and longest elapsed wait for each case. The dashboard-only
`GET /v1/event-logs/{log_id}/waiting-time/cases` takes the analysis settings and case filter,
plus the exact `source` and `destination` labels. Send `next_after` as `after` for the next
page. The optional `limit` is 1–100 and defaults to 50.

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
same cases. Click a dot or open a case from the table to review its recorded events.
One million events take well under a second to sample
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
- `min_cases` (default 30) requires at least this many sampled cases with directly-follows paths on
  each side of a tested boundary.

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

Use **Inspect sampled cases** on a window to review its analysis sample. The before and after
actions on a changed path select cases containing that exact adjacent pair within each sample.
Open a case to see its full event history. The dashboard-only
`POST /v1/event-logs/{log_id}/drift/cases` reuses the window settings and case filter in the query.
Send the zero-based `window_index` in the body. Pass the report's `sample_sha256` as
`expected_sample_sha256` on every page to review the same sample, and use `next_after` as `after`
for the next page. Other optional fields are `expected_window`, `source` and `destination`
together, and `limit` (1–100, default 50). An append or changes to the case selection, activity
mapping or hidden activities can require a fresh analysis even when the window label is the same.

Download the full drift report as JSON or the per-window summary as CSV. The dashboard and CSV
show sample sizes, the number of sampled cases with paths, and reasons for skipped boundaries.

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
| `GET /v1/event-logs/{log_id}/quarantine?batch=&after=&limit=` | Kept rows by batch and reason, a page of rows, and the first 100 decisions with paging metadata. |
| `GET /v1/event-logs/{log_id}/quarantine/decisions?offset=&limit=` | A page of up to 100 recorded decisions; history can change between pages. |
| `POST /v1/event-logs/{log_id}/quarantine/preview` | Preview release eligibility for the supplied timestamp settings and scope. Returns a digest to include as `expected_preview_sha256` when releasing. |
| `POST /v1/event-logs/{log_id}/quarantine/release` | `{"naive_timestamp_offset": "+01:00", "batch": 0}` (batch optional). |
| `POST /v1/event-logs/{log_id}/quarantine/discard` | `{"batch": 0, "row_start": 2, "row_end": 5}` discards kept rows in that inclusive range. Use just `batch` for one batch or `{}` for all kept rows. |

A release can use the default ISO 8601 parser or a `timestamp_format` pattern such as
`%d/%m/%Y %H:%M`. Include `naive_timestamp_offset` (`Z` or `±HH:MM`, at most ±14:00)
when the timestamps omit their offset. A declared pattern containing `%z` reads each value's
own offset. Given a batch, `row_start` and `row_end` scope preview and release to an inclusive
range. The preview digest binds the format, offset, scope, and retained rows.

Kept rows that become valid are appended through the normal append path, which skips events
already in the log. The new batch's quality report (`kind: quarantine_release`) records the format
and any assumed offset. Rows that still fail, such as rows without a case, stay kept. Each release
and discard decision records its counts. If the append succeeds and removal of the released rows
is interrupted, retrying the release uses the same duplicate checks.

In the dashboard, use **Select row** to fill the batch and range for a single retained row, or enter
the batch and range yourself. **Discard kept rows** asks you to confirm the scope before deleting
the retained rows. The decision history records the selected scope and the actual number discarded.

The same decision can be made up front for a CSV import or append with
`naive_timestamp_offset=+01:00`. It is part of the transform and its digest, and the report counts
the times read with the assumed offset.

## Private process map (differential privacy)

"Share a private process map" creates a directly-follows map that can be given to someone else
without revealing any single case. It follows privacy-preserving process mining: Mannhardt et al.
2019, the Libra and SaCoFa work of Elkoumy et al., and PM4Py's privacy module. The guarantee is
ε-differential privacy with respect to adding or removing one case.

1. **Bounded contribution.** Each case contributes at most `k` directly-follows pairs (default 10,
   from 1 to 100) and `k + 1` activity occurrences. The pairs include `(start) → A` and `B → (end)`.
   A longer case keeps a random subset, drawn anew for each export.
2. **Laplace noise.** The budget ε (0.01–10, default 1) is split evenly:
   - path counts get noise of scale `2k/ε`;
   - activity counts get noise of scale `2(k+1)/ε`.

   Noise is added to every possible pair over the log's activity labels, observed or not, so
   whether an edge appears does not reveal that a case took it.
3. **Release threshold.** Counts below the threshold are left out and the rest are rounded. By
   default the threshold keeps the expected number of released pairs that no case took at 0.05 per
   export, and likewise for activities. A fixed `threshold` can be given instead.

The export contains activities, edges and approximate counts, and nothing else: no case
identifiers, times, resources, attributes or durations.

**Activity names are released as they are.** They come from the log's label set and the noise does
not protect them. Rename labels that would themselves identify someone before sharing.

**Repeated exports add up:** two exports at ε each spend 2ε in total. The owner also sees how many
cases there are and how many exceeded the cap, to help choose `k`. That figure is not private, so
it is kept out of the download (`owner_only_not_private`).

`POST /v1/event-logs/{log_id}/private-map?case_type=` with
`{"epsilon": 1, "max_transitions_per_case": 10, "threshold": null}`. It is a POST because each call
draws fresh noise and spends budget. Dashboard session only. It covers every case of the log, not
the case filter.

## Case filter and playback

In **Case duration distribution**, choose **Inspect cases** on a nonempty band to open its case
histories. **Use band as filter** narrows the active workspace filter to that band while retaining
the other conditions. A band includes its lower bound and excludes its upper bound. The distribution
measures elapsed time between the first and last retained event of each case.

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

**Time between the two steps.** A rule where one step follows the other can also limit the time
between them: **after at least** some hours and/or **within** some hours (`min_hours` inclusive and
`max_hours` exclusive in the rule, for example
`{"from": "Submit", "to": "Approve", "mode": "eventual", "min_hours": 48}` for approvals that took
two days or more). For *eventually*, the time runs from the first occurrence of the first activity to
the next occurrence of the second after it; for *directly*, it is the first place where the second
activity comes right after the first. A case where the step never follows matches no time limit.
Limits cannot be set on *never* rules. Saved filters keep them. This is the throughput filter on a
segment in Disco and Celonis.

**Case attributes in the filter.** The dashboard's case filter also takes one case-attribute value,
written `name=value` (for example `Region=EU`), and a numeric range on one case attribute (from,
inclusive, and/or below, exclusive). These are the `attribute` and `attribute_range` query
parameters (`attribute_range={"name":"amount","min":200,"max":400}`). Attributes come from the
import (OCEL object attributes) or from a joined case table. They combine with the follower rules,
every condition must hold, and they apply to the same views. Values that are not numbers never
match a range. The inputs are validated before anything is sent. Saving the filter keeps the
values and ranges with it.

**Endpoints and case duration.** The case filter also keeps cases that start with, or end with,
an exact activity, in recorded order (time, then event identifier). This is the endpoints filter of
Disco and Celonis. It can also keep cases whose duration lies in a range: the time from the first to
the last recorded event, at least the lower bound and below the upper bound. This is Disco's
performance filter. The dashboard takes hours; the API takes seconds.

The query parameters are `starts_with`, `ends_with`, `duration_min` and `duration_max`. In a JSON
cohort they are `starts_with`, `ends_with` and `duration: {"min": s, "max": s}`. They combine with
every other condition and apply to the same views. Saved filters keep them. A saved filter whose
first or last activity is no longer in the log is flagged, like its follower rules.

**Resource and event attributes.** Keep cases that contain an event with the
exact resource label or an event whose retained attribute matches `name=value`.
Use **Find labels** to select an exact resource label in the current case notion.
When both fields are set, they can match different events within the same
case. The workspace keeps the full sequence for every matching case.

**Case start dates.** Select UTC dates to keep cases whose first recorded event
falls on or after the lower date and before the upper date. The selection keeps
each matching case's full event sequence and can be saved as a filter. When a log
comparison uses the workspace filter, its baseline dates narrow this selection.

**End time, events, path frequency and rework.** Four more filters follow Disco's timeframe,
performance and variation filters:

* **Case ended**: the last recorded event is on or after one date and/or before another
  (`ended_from`, `ended_before`; timezone-qualified ISO times in the API, dates in the
  dashboard). This complements the start-period filter, for example to keep cases that finished
  last month.
* **Events per case**: at least and/or at most a number of recorded events (`events_min`,
  `events_max`; both inclusive).
* **Path frequency**: keeps cases whose exact path (sequence of activities) is followed by at
  least and/or at most a number of cases (`path_cases_min`, `path_cases_max`; both inclusive).
  "At least 10" keeps the mainstream paths; "at most 2" keeps the rare ones. The count is taken
  over all cases under the case notion, before the other filters apply, so it does not change
  when another filter is added.
* **Rework**: `rework=true` keeps cases that repeat at least one activity; `rework=false` keeps
  cases that repeat none.

* **Case tag**: keeps the cases that carry a tag (`tag`) or that do not (`without_tag`), for
  example everything except the cases tagged as not fitting a reference net. Tags come from *Case
  tags*, unusual cases, alignments and root-cause results.
* **Case never does activity**: `without_activity` keeps the cases in which an activity never
  occurs. It is the opposite of *contains activity*, for example every case without an
  escalation, or (combined with *contains activity*) the cases that were approved but never
  shipped. The label must match exactly.

In a JSON cohort they are `ended_from`, `ended_before`, `events: {"min": n, "max": n}`,
`path_cases: {"min": n, "max": n}`, `rework: true|false`, `tag`, `without_tag` and `without_activity`. They combine with every other
condition, apply to the same views, and are kept by saved filters.

The case pickers inside individual sections take the same numeric range, under "Numeric case
attribute", "From (inclusive)" and "Below (exclusive)". These are KPIs, the cohort benchmark and
map comparison, the saved-graph comparison, case tags, saving cases as a log, case metrics and
case evidence. A range is validated in the section before anything is sent.

After an analysis, every case-attribute input suggests what the log contains. `name=value` inputs
offer each attribute's 20 most common values with their case counts, and numeric-range inputs offer
the numeric attributes with their minimum and maximum. This covers the global filter and the
pickers inside sections. The suggestions come from
Use **Find a case attribute** in the workspace case filter to search by part of
a case attribute name, including names outside the initial suggestion list.
Search returns up to 20 names and reports the total. Choose a common value or a
numeric field to populate the filter before applying it.

`GET /v1/event-logs/{log_id}/case-attribute-values?case_type=...&search=...`
(`openworkgraph.event-log-case-attribute-values.v1`): up to 100 attribute names per case notion,
each with its case count, distinct values, whether it is numeric (90% or more of its values are
numbers) and its top values, skipping values longer than 100 characters. Nothing is stored.

An automatic insight about a case-attribute group (`attribute_segment`) and a root-cause row for
an attribute value or range both offer to become this filter in one click: "Filter to these cases"
and "Filter". They are offered only while no global filter is active, so they never silently
replace one. They set exactly the finding's `attribute` or `attribute_range`.

When importing a filter from another log, preview it first to check the matching
case count, case notion, and activity labels unavailable in the destination.
Importing then saves the reviewed definition with that log. The preview endpoint
is `POST /v1/event-logs/{log_id}/filter-sets/import/preview`.

**Saved filters.** A new dashboard filter remembers the selected case notion.
Applying it restores that notion and the filter together. Older filters saved
without a case notion use the current selection.
The active case filter can be saved under a name and applied again later from
the same panel; applying it re-runs every filtered view at once. The API stores any case filter
(`{"name": "...", "cohort": {"follows": [...], "started_from": "...", ...}}`, up to 50 per log,
names unique regardless of case) as `openworkgraph.event-log-filter-set.v1`. The dashboard panel
applies follower rules, case-attribute values, numeric ranges, endpoints and case duration. A saved filter is re-applied to the log as it is now, so the listing says
when newer exports were appended after it was saved and names any activities it uses that the log
does not contain. Saved filters are deleted with the log and never change events.

Use **Preview** to check the current match count and see up to five case keys
before applying a saved filter. The preview uses the saved case notion, or the
current case notion for an older filter. Activity availability reflects hidden
activities and the active mapping.

Use **Export JSON** on a saved filter and **Import a filter from another log**
on the destination. The JSON carries the filter name, rules and saved case
notion. Import validates the rules and case notion against the destination log.
Filter names are unique within each log.


The case evidence drill-down also uses the active case filter, so browsing cases shows the same
cases the filtered views count ("N matching cases (case filter applied)"). Object-type maps keep
their own case notion and are not filtered.

**Case playback** animates cases over the process map. `GET /v1/event-logs/{log_id}/playback` returns
each case's events as seconds from the log's first event, restricted to the same most frequent
activities that the map shows for the same `activities` share. At most `max_cases` (default 200, up
to 500) cases are returned, sampled evenly by start time, and at most 30,000 events in total. The
dashboard moves one token per case along the drawn arrow between consecutive events. A step whose
arrow the paths slider hid moves in a straight line, and steps that share a timestamp jump instantly;
the status line counts both. Choose a sampled case to isolate its token and scrub within its
displayed time span. **Open selected case** shows the case's recorded events. Play, pause and
the position slider work from the keyboard. With reduced
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

## Comparing two logs

**Compare event logs** (after **Analyse**) compares the selected log with another saved log
or two case selections within the same log. Typical uses are two regions exported separately
or the same process before and after a system migration. The REST form accepts the same scopes:

```json
{"baseline": {"log_id": "log_…", "case_type": null, "cohort": {}},
 "comparison": {"log_id": "log_…", "cohort": {"started_from": "2024-07-01T00:00:00Z"}},
 "limit": 25}
```

Choose the comparison log's case notion and use **Case selection** to set separate UTC start-date
ranges. The lower date is inclusive and the upper date is exclusive. The baseline can also use
the current workspace case filter. **Download comparison JSON** saves the displayed results,
including the case notions and applied filters, to a local file.

Select a case count to review cases from that side of the report. The activity, path and
variant tables list only cases that match that row and the side's case selection. Select a
supplied case key to see its event history. Case keys and event rows are available in the
local dashboard. The aggregate JSON omits case keys and events. Use **Download
baseline events (CSV)** or **Download comparison events (CSV)** to save the
complete original traces for that side's case selection and case notion. Open a case in
either the comparison or the shared inspector to download its complete trace
using **Download case events (CSV)**. The download includes original retained
events beyond the current page of the inspector.

`cohort` takes the same keys as other analyses. `limit` (1–200) caps each ranked
list. The response contains:

* **KPIs for each side and their difference:** cases, events per case, the
  share of cases that repeat an activity (with a 95% interval for the
  difference), and case-duration quantiles. It also gives the probability that a
  random comparison case takes longer than a random baseline case (0.5 means no
  shift).
* **Activities and paths:** those present only in the baseline, those present
  only in the comparison, and the largest differences in the share of cases
  that contain each one. Each difference has a Newcombe 95% interval.
* **Variants:** how many variants occur in both logs and what share of each
  log's cases they cover, plus the variants whose share differs most. Each ranked
  variant includes an exact identifier for the existing case-query endpoint.

Activities are matched by exact name after each log's applied activity
mapping. Define a mapping to harmonize names that differ between source
systems. The comparison is descriptive: the two logs may differ in extraction,
coverage and case mix, so a difference is not evidence of its cause. The
intervals are not corrected for the number of rows shown.

## AI agent runs as a log (agent mining)

Process-mining vendors now analyse AI agents' work alongside people's. SAP Signavio calls this
agent mining. OpenWorkGraph already records the structure of observed agent runs (see
`get_agent_runs`). "AI agent runs as a log" turns those runs into an ordinary event log, so
variants, KPIs, root cause, conformance, filters and the rest work on agents too.

- **Case:** one observed run, with its opaque execution ID. Native run, trace and session IDs are
  not used.
- **Events:** structural steps.
  - Run started; Run finished, or Run failed.
  - Tool calls as `Tool: <name>`, with the status in brackets when the call failed.
  - Approvals, handoffs and errors.
  - Model calls only when you tick "Include model calls".
  - Each event's attributes are status, tool category and step seconds. The resource is the agent.
- **Case attributes:** Outcome, Agent, Provider, Framework, Model, Tool calls, Failed tool calls,
  Approvals requested, Input tokens, Output tokens, and Complete boundary ("no" when the start or
  finish was not observed).
- **Privacy:**
  - No prompt, tool argument, tool result, file path or message text is copied.
  - Tool names use the same privacy-safe labels as the agent-run report: known runtime and MCP tool
    names stay readable, and other names become stable opaque hashes.
  - The log is stored only after you confirm local storage, like any import.

`POST /v1/event-logs/import-agent-runs` takes a dashboard session and the local-storage
confirmation header. The JSON body has `name`, plus optional `since`, `until` (times with an
offset), `max_runs` (1–5,000, default 1,000) and `include_model_calls`. Each run keeps at most 500
steps, and the answer counts runs that were cut. A run still in progress is left out until it
finishes or has been quiet for 30 minutes.

### Keeping the agent-runs log up to date

A log made from agent runs remembers that. Its "Keep agent runs up to date" section can **Add new
runs now**, or **Add new runs every day** with the local scheduler (job kind
`event_log_agent_runs`). A refresh appends only settled runs that are not in the log yet, with
their case attributes. It looks back 7 days before the last refresh, so runs that finish late are
still caught. Runs already in the log are not changed. Together with signal rules on the same log,
for example the share of runs with Outcome = error, this monitors your agents continuously.
Deleting the log removes its schedule.

`GET /v1/event-logs/{log_id}/agent-runs` reports whether the log was made from agent runs and when
it was last refreshed. `POST /v1/event-logs/{log_id}/agent-runs/refresh` needs a dashboard session
and the local-storage confirmation header.

## Automation candidates

"Automation candidates" ranks activities by evidence an event log can show. Vendors such as
Celonis, UiPath and SAP Signavio rank automation potential in a similar way. Every component is
returned:

| Component | Meaning |
| --- | --- |
| volume | Occurrences per week, on a log scale relative to the busiest activity. |
| routine | Average of how often the activity follows its most common predecessor and is followed by its most common successor. |
| time | Share of all waiting in the log that comes right before this activity. |

`score = 100 × (0.4 volume + 0.4 routine + 0.2 time)`

Each activity also reports occurrences, cases, case share, repeats per case, the median wait
before it, resources, the top resource's share, and a routing pattern:

- **fixed**: the same neighbours in at least 80% of occurrences, and rarely repeated. Rules may
  cover it.
- **mostly fixed**: the same neighbours in 60–80% of occurrences. Rules with exceptions.
- **variable**: routing varies. Judgement is involved, and an AI agent with human review may fit.

Activities seen in fewer than 5 cases are left out and counted. The global case filter applies.
"Add to automation pipeline" creates an idea in the automation pipeline with
`source.kind = "event_log"` and the evidence in its description, ready for the ease and impact
assessment there.

Waiting is the gap since the previous event, not working time. The ranking estimates no savings
and authorizes nothing.

`GET /v1/event-logs/{log_id}/automation-candidates?limit=&case_type=` plus the usual case-filter
parameters; dashboard session only.

## Performance spectrum

The performance spectrum shows how long each step-to-step segment took, over calendar time. It
follows Denisov, Fahland and van der Aalst (2018), as in ProM. A segment is "activity A directly
followed by activity B" within a case. Each plotted occurrence is a line from the moment A
happened (top axis) to the moment B happened (bottom axis).

This shows patterns a process map hides:
- **Batching:** many lines meet at one point at the bottom, so work was released together.
- **Overtaking:** lines cross, so later work finished before earlier work.
- **Changes over time:** some periods are slower than others.

For the 6 busiest segments (1–12), or the segments asked for, the result gives:

| Field | Meaning |
| --- | --- |
| `duration_seconds` | 25th, 50th, 75th and 95th percentiles, mean and maximum. |
| `bins` | For equal calendar periods by start time (2–100, default 30): occurrences per class and the median. |
| `bin_width_seconds` | Width of each period. Period 0 starts at `time_origin`. |
| `fifo_share` | Among occurrences in start order, the share that also finish no earlier than the one before. Pairs that start at the same instant are skipped. `null` when no two start at different times. |
| `batched_share` | Share of occurrences that end at the same instant as at least two others of the segment. |
| `lines` | A repeatable sample of at most 400 occurrences with start, end (seconds from `time_origin`) and class. `lines_sampled` says when it is a sample. |

Choose **Inspect cases** for an entire segment, or select a count under **Occurrences per
period** to review one period and duration class. The list includes all matching occurrences,
including those outside the chart sample. Repeated transitions are counted under the same
case, with its first start, last finish and median duration. Open a case to see its event history.

Each occurrence is put in a class against the segment's own percentiles:
- `fast`: below the 25th percentile;
- `normal`: between the 25th and 75th;
- `slow`: above the 75th;
- `very_slow`: above the 95th.

Durations are the time between two recorded events, not working time. Overtaking and batching
describe the log; they do not say why work waited or who caused it. The global case filter
applies.

`GET /v1/event-logs/{log_id}/performance-spectrum?top=&bins=&segment=A->B&case_type=` plus the
case-filter parameters. Repeat `segment` for several segments (at most 12). For case evidence,
`POST /v1/event-logs/{log_id}/performance-spectrum/cases` with the same `bins`, case notion and
case filter. Send the exact activity pair as `{"segment":["A","B"]}`, with optional `bin`
(zero-based) or duration `class`. Results are ordered by literal case key with `limit` 1–100
(default 50). Use the returned `next_after` as `after` to continue. Both routes require a
dashboard session.

## Heuristics net

**Heuristics net** (the heuristics miner of ProM and PM4Py, after Weijters and Ribeiro's Flexible
Heuristics Miner) draws a model that tolerates noise. It keeps an arrow *a → b* only when *a* is
directly followed by *b* clearly more often than the reverse:
dependency = (|a>b| − |b>a|) / (|a>b| + |b>a| + 1), kept at or above the threshold (default 0.5;
the section offers 0.3, 0.5, 0.7 and 0.9) and when seen at least the chosen number of times.

- **Self-loops** use |a>a| / (|a>a| + 1); **two-step loops** (*a b a*) are found from *a b a* patterns
  so that *a ⇄ b* is not dropped as two-way noise.
- **All connected** (default): every activity keeps its best incoming and outgoing arrow even below
  the threshold, unless it always starts or always ends a case.
- **Splits and joins:** two branches *b* and *c* after *a* are **AND** (parallel) when
  (|b>c| + |c>b|) / (|a>b| + |a>c| + 1) is at least 0.65, otherwise **XOR** (choice); joins likewise.
- The section draws the arrows, lists splits, joins and loops, and every arrow with its dependency
  and why it was kept. Logs with more than 64 activities need an activity mapping or a case filter
  first. The case filter applies.

Choose **Inspect cases** for a kept arrow, loop, or start or end boundary. The case list reports
the number of matching occurrences, including repeated and overlapping loops. Open a case
to see its recorded event history.

`GET /v1/event-logs/{log_id}/heuristics-net?dependency_threshold=&and_threshold=&loop_two_threshold=&min_occurrences=&all_connected=`
plus the case-filter parameters. Agents with access to a shared log use `get_process_heuristics_net`.

The dashboard-only `POST /v1/event-logs/{log_id}/heuristics-net/cases` keeps the report's case
notion and filter. Send `group` as `transition`, `length_two`, `start`, or `end`, and `activities`
as the two literal labels for a transition or back-and-forth loop, or one for a boundary.
For a repeated activity, send a transition with the same label twice. Send the returned
`next_after` as `after` to page by literal case key. The optional `limit` is 1–100 (default 50).

## Activity profile

**Activity profile** shows one activity from every side, like the activity views of Celonis and
Disco: how many cases have it and how many repeat it, what usually comes before and after it with the
median gap, how far into the case it first happens, how often it occurs on each weekday and hour
(UTC), and how long cases with it take compared with cases without it. A longer duration with the
step is an association, not proof that the step causes it.

Use **Exact activity** to profile a label beyond the frequent-activity suggestions. Review cases
with, without, or repeating that activity, or open cases with an adjacent transition shown in
the profile. The case list keeps the profile's case filter. Case boundaries are shown separately
from activities literally named `(start)` or `(end)`. The profile API marks boundary rows with
`boundary: true`.

`GET /v1/event-logs/{log_id}/activity-profile?profile_activity=Check` plus the case-filter
parameters (`activity` there is the case filter). Agents use `get_process_activity_profile`.

## Activities that happen together

**Activities that happen together** compares the most common activities (8, 12 or 20) by the
cases they occur in, in any order. The matrix reads row by row: of the cases with the activity in
the row, the share that also have the one in the column. Hover a cell for the number of cases and
the **lift**: cases with both × all cases / (cases with the first × cases with the second). Above 1,
the two go together more than chance would give; 0 means they never occur in the same case.

Below the matrix are the pairs that **go together most** (lift above 1, with at least 5 cases with
both) and the pairs of activities, each in at least 5 cases, that are **never in the same case**.
The second list often shows alternative branches, such as approve and reject. The view honours the
case filter. `GET /v1/event-logs/{log_id}/co-occurrence?limit=12&min_support=5` (plus `case_type`
and the case-filter parameters) needs the dashboard session. Co-occurrence describes the recorded
cases; two activities that go together need not depend on each other.

Select a first and second activity below the matrix to inspect cases containing both, or cases
containing just one of the pair. Case evidence keeps the matrix's case filter and opens each
matching case's complete history. The dashboard-only `POST /v1/event-logs/{log_id}/cases/query`
accepts `required_activities` and `excluded_activities`. Each list accepts up to 10 exact labels.
A case must contain every required label and none of the excluded labels, regardless of order.

## Path explorer

**Path explorer** walks the process one step at a time, as Celonis's Process Explorer and the variant
trees of UiPath Process Mining and ProM do. It starts at the beginning of the cases and lists the
activities that come first; choosing one shows what comes after it, and so on. For each next step it
shows the number and share of cases that continue with it, the hours from the previous step (median
and 90th percentile), the median hours from it to the case end, and how many cases end at this point
(`(end)`). The path chosen so far is shown as a trail, with a button to step back.

**From anywhere in the case** looks for the first place the chosen steps occur one right after
another, instead of requiring the case to begin with them, which answers "what usually happens after
Reject → Rework?". Continuing down a branch keeps that first occurrence fixed in each case. If a
case repeats the sequence later, the later occurrence does not change which branch it took.

Use **Inspect cases** on a branch to see the matching case keys and event counts, then open a
case to review its event history. This works for branches where the case ends as well.

`GET /v1/event-logs/{log_id}/next-steps?step=Open&step=Check&anchor=start` plus the case-filter
parameters. Agents use `get_process_next_steps` (aggregates only) and call it again with a step
appended to go deeper.
For anchored continuation with `anchor=anywhere`, pass `anchor_steps`, the length of the initial
sequence. The API's `is_end` field distinguishes a case ending from an activity named `(end)`.

The dashboard-only `POST /v1/event-logs/{log_id}/next-steps/cases` uses the same query
parameters as the summary. Send `{"activity":"Check"}` for a continuation or `{"ends_here":true}`
for a case ending. Each response gives the exact branch total and up to `limit` cases (1–100,
default 50) in literal case-key order. Send the returned `next_after` as `after` in the next body.
Each page reflects the current log.

## Due dates

Use **Inspect cases** next to a deadline group to view its case keys, due and completion times,
and links to event history. The dashboard-only `GET /v1/event-logs/{log_id}/due-dates/cases`
takes the due-date settings and case filter from the summary, plus `due_group`. Send the returned
`next_after` as `after` to continue in literal case-key order. The optional `limit` is 1–100
(default 50), and each page reflects the current log.

Case filters choose which cases appear. Open cases are evaluated at the latest event in the
selected case notion, so filtering to cases that are still open preserves the observation cutoff.

Set **As of (UTC, optional)** to reconstruct a deadline report using only events at or before that
time. Event-based filters and completion checks use that truncated history. For attribute deadlines,
the report uses values recorded on events by the cutoff. Joined or object-level attributes lack an
observation timestamp and remain available in the default current report. Case tags and case-level
filter attributes reflect the current workspace. Each case page uses the report's cutoff, and the
linked case history shows the full retained record. JSON and CSV downloads contain aggregate scores
and the assessment time. Pass an offset-qualified `as_of` to either dashboard due-date API.

**Due dates** checks whether the step that matters, such as *Pay invoice* or *Ship order*, happened
by the date it was due, as UiPath Process Mining's due dates and Celonis's on-time views do. The due
date is either an attribute (a joined case attribute, or else the first value of an event attribute in
the case, such as a payment due date column) or a fixed number of days after the case started. The
first occurrence of the activity in each case is compared with its due date:

- **on time**: by the due date (a date without a time is due by the end of that day, UTC);
- **early**: with an early window, more than that many days before the due date;
- **late**: after the due date, with the days late (median, 90th percentile, maximum);
- **open, overdue**: not done, and the due date is before the log's last event;
- **open, not yet due**: not done, and the due date is still ahead;
- **no due date** and **unreadable due date**: counted, never guessed.

A table by due month shows whether adherence is changing, and the most late cases are listed. The
report says how often deadlines were met, not why.

**Alert me** saves the same check as a **due-date signal**: it holds when more than the chosen
number of open cases are past their due date. Signals can also watch the late share among cases where
the activity happened (`"metric": "late_share"`, a fraction). The rule is
`{"kind": "due_date", "activity": …, "due_attribute": … or "due_days": …, "metric":
"open_overdue_cases" | "late_share", "operator": "above" | "below", "threshold": …}`.

`GET /v1/event-logs/{log_id}/due-dates?due_activity=Pay&due_attribute=due` (or `due_days=30`,
optionally `early_days=7`) plus the case-filter parameters, in which `activity` keeps cases that
contain an activity. Agents use `get_process_due_dates`; case IDs only for
logs shared at the cases level.

## Stuck open cases

The case filter selects the open cases to check. The observation cutoff is the latest event in the
selected case notion, and the usual waits come from observed next events in that same case notion.
Choose **Open case** to review the event history behind a flagged case.

`POST /v1/event-logs/{log_id}/stuck-cases` takes `end_activities`, `percentile`,
`min_support`, and the optional `cohort` and `case_type`. End activities match their exact
labels. The response gives the total flagged count and up to `limit` cases (default 50,
maximum 500).
In the dashboard, use a JSON array to enter an end activity with a comma or surrounding spaces.

## Unusual cases

**Unusual cases** ranks the cases that stand out from the rest of the selection, as the outlier
and anomaly views of Celonis and UiPath Process Mining do, and says why for each one. Every case is
compared with the selection on transparent signals:

- **Rare path**: under 1% of cases (or only this one) followed exactly the same activities, and
  paths that rare cover at most 5% of cases. In a log where most paths are unique, being unique is
  not unusual and is not flagged.
- **Unusually slow / fast**: the case's duration is at least 3 robust standard deviations from the
  usual, measured on log-duration with the median and median absolute deviation, so a few extreme
  cases do not hide each other.
- **Unusually many events**: the same test on the number of events.
- **Heavy rework**: the same test on the number of repeated activities (at least two).
- **Rare activity**: the case contains an activity found in under 1% of cases, or in no other case.

Each signal adds 0.25 to 1 to the score; robust-z signals grow from 0.25 at the threshold to 1 at
twice it. The list shows ranked cases, their reasons, and the median case for comparison.
Use **Reason** to narrow the rows while keeping the original scoring baseline. **Explain** opens
the case in **Explain a case**. Each page starts with its rows selected. Clear the selection or
choose individual cases before tagging them. At least 20 cases are needed.

`GET /v1/event-logs/{log_id}/unusual-cases?limit=25` takes the case-filter parameters and optional
`signal`. Send `page.next_offset` as `offset` for the next page. Each page evaluates the current
log. Agents use
`get_unusual_process_cases`; case IDs and paths are included only for logs shared at the cases
level.

## Milestone funnel

**Milestone funnel** follows cases through 2 to 8 activities you choose, in order, as Celonis's
conversion views, UiPath Process Mining milestones and product-analytics funnels do. A case reaches
step *k* when it did steps 1 … *k* in that order, each after the previous one; other activities may
come in between. For each step the section shows:

- how many cases reached it, as a share of all cases and of those that reached step 1;
- the conversion from the previous step and how many cases dropped after it;
- the hours from the previous step (median, 90th percentile, mean);
- for cases that reached the previous step but not this one, the activity they did last, and how
  many of them ended in the last 5% of the log's time span and so may still be open.

The step with the largest drop is named, along with the median and 90th percentile time from the
first to the last step. An optional **conversion window** counts a step only within that many hours
of the latest occurrence of the previous step. The funnel shows how far cases got and how long it
took, not why the others stopped. Choose a step and either **Reached step** or **Stopped before
step** to inspect up to 50 matching case IDs, last recorded activities and steps reached. The
full matching count and any truncation are shown; evidence uses the same filter and conversion window.

`GET /v1/event-logs/{log_id}/funnel?step=A&step=B&within_hours=24` plus the case-filter
parameters. Dashboard sessions can additionally request `evidence_step=2&evidence_status=dropped`
(or `reached`); the first 50 matching case IDs in sorted order are returned with a total and
truncation flag. Case evidence is not available through the shared agent tool; agents with access
to a shared log use `get_process_funnel` (aggregates only).

## Model change

**Model change** shows how the discovered process model differs before and after a date, as ProM's
model comparison and Celonis's process comparison do; concept drift says *when* behaviour changed,
this says *how*. Cases are split by when they started (by default at the median case start), a
model is discovered for each period at the chosen noise level, and the section shows:

- the pairs of activities whose ordering relation changed (→, ←, || or #, as in **Footprints**);
- activities that only one period's model has;
- **cross-fit**: each period's cases aligned on both models. When the later cases fit the earlier
  model clearly worse than the earlier cases did, the way the work runs has changed.
- dashboard case evidence for each cross-fit row, including the aligned, excluded and unaligned
  case counts. Open a listed non-fitting variant to see cases with that exact recorded path in
  its original period or group. The dashboard lists up to 20 non-fitting variants per alignment.

Each period needs at least 10 cases; a start-date case filter cannot be combined with the split.
Both models are drafts from their own cases, and a change in how events are recorded looks the same
as a change in the work.

`GET /v1/event-logs/{log_id}/model-change?split=2024-03-01&noise=0.2` plus the other case-filter
parameters. Agents use `get_process_model_change` (aggregates only).

**Compare groups** in the same section runs the comparison between two groups of cases chosen by
case attribute values, such as `region=EU` and `region=US`, as Celonis compares org units (a value
no case attribute has is looked up in the cases' event attributes instead): the
orderings that differ, activities only one group has, and how well each group's cases fit the other
group's model. Each group needs at least 10 cases, and an attribute case filter cannot be combined
with the groups. Groups can also differ in case mix and in how their systems record events.
`GET /v1/event-logs/{log_id}/model-comparison?group_a=region=EU&group_b=region=US`; agents use
`compare_process_models`.

## Model quality

**Model quality** discovers the model at several noise levels (by default 0, 10%, 20%, 30% and 50%),
converts each draft to a Petri net as the PNML export does, and scores it on the four quality
dimensions of process mining (Buijs, van Dongen and van der Aalst, 2012), as PM4Py's evaluation
does:

- **Fitness**: token-replay fitness of the recorded cases, as in **Check against a Petri net**.
- **Precision**: ETConformance, one minus the share of escaping edges.
- **F-score**: the harmonic mean of fitness and precision.
- **Generalization**: `1 − mean over transitions of 1/√(times fired)`, where a transition never fired
  counts as 1. Parts of the net that few cases use score low.
- **Simplicity**: `1 / (1 + max(0, mean arcs per node − 2))`.

The noise level with the highest F-score is marked; on a tie the simpler net, then the lower noise.
Noise 0 keeps every observed path, which usually maximizes fitness at the cost of precision and
simplicity. The scores compare drafts against this log; they help choose the noise setting but do
not say which model is right. Up to eight levels can be scored at once.

`GET /v1/event-logs/{log_id}/model-quality?levels=0,0.1,0.2&activities=1` plus the case-filter
parameters. Agents with access to a shared log use `get_process_model_quality` (aggregates only).

Choose **Download PNML** on a score row to keep the exact Petri net used for that score.
**Replay scored net** opens that net in the Petri net check against the current retained case
selection. JSON and CSV score reports include coverage counts and PNML digests. On the dashboard
API, `include_nets=true` adds the PNML and its SHA-256 digest per row and the case filter used.

## Footprints

**Footprints** (the footprint matrices of van der Aalst's *Process Mining*, PM4Py's footprints
discovery and conformance, ProM's footprint comparison) classify every ordered pair of activities:
`→` when the first is directly followed by the second somewhere and never the other way round, `←`
for the mirror, `||` when both orders occur (or the model runs them in parallel) and `#` when
neither does.

The matrix is built twice: from the recorded cases, and from the process tree the model discovery
drafts (the same draft exported as BPMN and PNML), over the activities that model shows. The
**Footprints** section reports the share of cells that agree (footprint fitness), lists each
ordering only the log has with how many cases took it, each ordering only the model allows, and
start or end activities only the log has, and shows the full matrix with disagreeing cells marked.
The *Model* choice is the discovery noise filter: at 0 the model keeps every ordering it can.
**Activity coverage** controls which activities the model displays, based on their share of events.
The differences table shows up to 40 orderings and reports the full difference count.
Choose **Inspect cases** to review a log-only ordering or a start or end activity. The list uses
the same activity projection and case filter as the table and shows when excluded activities
changed an adjacency or boundary. Open a case to see its original event history.

Footprints compare order only: not frequency, timing or how often a loop repeats. A disagreement
shows where the draft and the recorded cases differ, not which one is right.

`GET /v1/event-logs/{log_id}/footprints?noise=&activities=` plus the case-filter parameters. Agents
with access to a shared log use `get_process_footprints` (aggregates only).

`POST /v1/event-logs/{log_id}/footprints/cases` keeps the case-filter parameters from the report.
Send `retained_activities` from the report's `activities`, plus a `group` of `transition`, `start`
or `end`. The body also needs `activities` containing the two transition labels or one start or
end label. The dashboard-only response gives the exact totals and cases in literal key order.
Send `next_after` as `after` to continue, and use `limit` from 1 to 100 (default 50).

## Check against a Petri net (PNML import and token replay)

ProM, PM4Py, Apromore, WoPeD and most academic tools exchange models as Petri nets in PNML. The
**Check against a Petri net** section reads such a file (at most 2 MB; not stored) and replays the
recorded cases on it with token-based replay (Rozinat and van der Aalst, 2008), as PM4Py's
`token_based_replay` does. Each case starts from the net's initial marking and fires the
transition labelled with each activity. Silent (invisible) transitions fire when a bounded search
finds them enabling the next step or reaching the final marking; otherwise the missing tokens are
created and counted. At the end the final marking is consumed and any tokens left are counted as
remaining.

Fitness is `½(1 − missing/consumed) + ½(1 − remaining/produced)`, for each case and over the whole
log. The section lists the places where tokens went missing or were left behind, the steps taken
when the net did not allow them, activities the net does not have (skipped and counted), net
transitions no case took, and the variants that do not fit.

Choose **Inspect cases** next to a variant in token replay or the step-by-step explanation.
The case list uses the exact observed path and the case filter from the report. Open a case
to see its recorded event history.

**Precision** (ETConformance, Muñoz-Gama and Carmona, 2010; PM4Py's `precision_token_based_replay`)
asks the opposite question: how much more the net allows than the cases did. For each prefix of
the recorded cases that replays without problems, the activities the net allows next, directly or
after silent transitions, are compared with the activities cases actually took next. Those no case
took are escaping edges. Precision is one minus the escaping share, weighted by how many cases
share each prefix. A net that allows anything in any order (a "flower") fits every case but has low
precision. The section lists the most common escaping edges. *Replay on the discovered net* runs the
same check on the net that **Process model discovery** exports, discovered with every observed
path kept (noise 0).

Transitions are matched to activities by their exact label. Silent transitions are those marked
with ProM's and PM4Py's `$invisible$` tool-specific flag. When the file declares no final marking,
cases are expected to end with one token on each place nothing consumes from. XML with a DOCTYPE
or entity declaration is refused. Token replay does not measure precision and can over-count
problems in nets with duplicate labels or long silent paths.

`POST /v1/event-logs/{log_id}/petri-net-replay` with the PNML file as the body, plus the
case-filter parameters.

**Check against the most common path** builds a sequence net of the most common path (the "happy
path") and runs the same replay on it, so fitness, precision, the step-by-step explanation and
**What goes with these deviations?** all compare cases with the usual way the work is done, as
Celonis's happy-path views do. `GET /v1/event-logs/{log_id}/variant.pnml?rank=1` returns that net
(`rank` 1 to 50 picks a less common path) with the share of cases that follow it in the
`X-OWG-Path-Cases` header.

**Save this net** keeps the net just checked as a named **reference net** with the log (at most 20
per log), and **Replay on saved net** checks the current selection against a saved one again, as
target-process libraries in Celonis and ProM's model collections do. Saved nets are checked when
saved (safe XML, a reachable final marking), listed by name and size, and deleted with the log.
`GET /v1/event-logs/{log_id}/reference-nets`, `POST …/reference-nets` with `{"name", "pnml"}`,
`GET …/reference-nets/{net_id}.pnml` and `POST …/reference-nets/{net_id}/delete`.

**Alert me** keeps the net that was just replayed (uploaded, discovered or the most common path) as a
**conformance signal**: it holds when the share of cases that do not fit that net is above the
chosen limit, among all cases or those started in the last 7 or 30 days of the log. The signal keeps
its own copy of the net (at most 60,000 characters), so later checks compare new cases with the same
reference even after the discovered model changes. Signals can also use the alignment fitness
(`metric: "alignment_fitness"`, usually with `operator: "below"`) and `minimum_cases`. Checking
signals aligns each distinct path once; agents listing signals see the rule without the net.

**Scorecard by month**, in the Signals section, shows every KPI, conformance and due-date signal
month by month, like a KPI cockpit: for the cases that started in each of the last 6 months, the
signal's value and whether its condition held. It is read-only and never changes a signal's state or
review. Case-matching and compliance signals are not shown by month.
`GET /v1/event-logs/{log_id}/signals/scorecard?months=6`; agents use `get_process_signal_scorecard`.

### Step-by-step explanation (alignments)

After a replay, **Explain each case step by step** aligns every variant on the same net (Adriansyah,
2014; PM4Py's and ProM's alignment-based conformance). An alignment pairs the steps of a case with a
complete run of the net. Each step is a *synchronous move* (the case did it and the net allowed it),
a *log move* (the case did a step the net cannot explain there), a *model move* (the net needed a step
the case skipped) or a free silent move. Log and model moves cost 1. A least-cost alignment is found
by a shortest-path search over the case and the net together, so the result names exactly which
steps were extra and which were skipped, without the over-counting token replay can show with
duplicate labels or silent paths.

Trace fitness is `1 − cost / (case length + visible steps in the net's shortest run)`, as in PM4Py.
Log fitness divides the summed costs by the summed worst cases. The section lists the activities
cases did where the net does not allow them, the activities the net required that cases skipped
(both with case counts), and how each non-fitting variant is explained, e.g.
`Register → −Check (skipped) → Pay → Close`. When the cases start in more than one month, a
**fitness by month** table shows, for each month the cases started, how many follow the net exactly
and the alignment fitness, so a change in adherence over time (for example after a process change)
is visible. Each variant is aligned once. Up to 500 variants of at
most 200 steps are aligned; the search has a per-variant and a total limit, and variants it cannot
finish are left out of the fitness. **Variants without a completed alignment** lists the available
paths from that search and links to their cases. The report also counts cases outside the length
or variant limits. When two explanations cost the same, one is shown.

`POST /v1/event-logs/{log_id}/petri-net-alignments` with the PNML file as the body, plus the
case-filter parameters. The net is not stored.
Agents with access to a shared log use `get_process_deviations`, which aligns the cases on the
discovered net at a chosen noise level (aggregates only, no case IDs).

**Compare with case** in **Explain a case** puts the explained case next to another one: their
steps aligned (shared steps line up; steps only one case has stand out), the hours from each
case's start to every step, and between which shared steps the second case fell furthest behind.
`GET /v1/event-logs/{log_id}/case-compare?case_a=…&case_b=…`; agents with cases-level access use
`compare_process_cases`.

**Explain a case** also aligns the chosen case on the discovered model (noise 20%) and says what it
skipped and which steps it did where the model does not allow them.
`GET /v1/event-logs/{log_id}/case-alignment?case_id=…&noise=0.2`; agents with cases-level access use
`align_process_case`.

### What goes with the deviations

After the step-by-step explanation, **What goes with these deviations?** runs root-cause analysis
with the outcome "the case followed a path that does not fit the net" (Celonis's conformance checker
shows the same for violations). Case attributes, activities and attribute ranges are tested as risk
differences with Newcombe 95% intervals and a Bonferroni check over the features tested, under the
same case filter as the alignment. Activities appear too: a case with an extra or rare step often
deviates because of that step. These are associations, not causes.

`POST /v1/event-logs/{log_id}/petri-net-deviation-causes` with the PNML file as the body, plus the
case-filter parameters.

**Download non-fitting cases (CSV)** lists every case that does not fit the net, one row each, for
follow-up in another tool: case ID, its activities, alignment fitness, the steps it added and the
steps it skipped. Cells starting with a formula character are prefixed with `'` so the file is safe
to open in a spreadsheet; at most 200,000 cases are written, and cases too long to align are left
out and counted in the `X-OWG-Non-Fitting` header.
`POST /v1/event-logs/{log_id}/petri-net-alignments/cases.csv` with the PNML file as the body, plus
the case-filter parameters. The net is not stored. Root-cause analysis also accepts the outcome `variant_in` with a list of
paths when called directly. Agents use `get_process_deviation_causes` on the discovered net.

**Tag the non-fitting cases** adds a case tag (default `off-model`) to every case that does not fit
the net, within the case filter. The tag then works like any other: filter every analysis to those
cases, compare them with the rest, or save them as a new log. Cases are chosen by their exact path;
the 500 most common non-fitting paths are covered, and cases on rarer ones are counted as not
tagged. Tagging again with the same tag does not duplicate it.
`POST /v1/event-logs/{log_id}/petri-net-alignments/tag?new_tag=…&note=…` with the PNML file as the
body, plus the case-filter parameters (`tag` there is the filter for cases already tagged, so the
new tag is `new_tag`). A tag is the analyst's label, not a finding.

## Temporal profile

**Temporal profile** (as in PM4Py and ProM) learns the usual time between every ordered pair of
activities in a case, not just neighbours: for *Order → Invoice* it records the mean and standard
deviation of the time from the order to the invoice, whatever happened in between. A case
deviates on a pair when its time is more than *zeta* standard deviations from the mean (default 3;
the section offers 2, 3, 4 and 6). This catches cases whose steps each look normal but that took
far too long, or suspiciously little time, overall between two points.

- Pairs are taken up to 50 events apart within a case.
- A pair is checked only when it occurs at least `min_occurrences` times (default 5) and its times vary.
- The section lists the pairs most often outside their usual time (with the allowed range and how many
  were slower or faster) and the cases with most unusual timings, each with its strongest one.
- The profile is learned from the same cases it checks, so a few extreme cases widen it; read the
  results as timings to review, not errors. The case filter applies.

Set **Baseline cutoff (UTC)** to check later cases against an earlier period.
OWG learns from cases whose last recorded event is before the cutoff, then checks
cases whose first recorded event is at or after it. It counts cases spanning the
cutoff separately and excludes them from both periods. The result shows how many
later cases have a checked pair and how many later pair occurrences lack a usable
baseline. Both periods use the active case filters and case notion. An optional
`baseline_cutoff` query parameter accepts a timezone-qualified ISO timestamp
on both the summary and the case review endpoints.

**Browse all deviating cases** opens the complete set of flagged cases. **Inspect cases** on
a pair limits the review to that pair. Either review can show slower or faster deviations and
open complete event histories.

The dashboard-only `POST /v1/event-logs/{log_id}/temporal-profile/cases` uses the same analysis
settings and case filter in its query. The body accepts optional exact `source` and `destination`
labels together, a `direction` of `all`, `slower`, or `faster`, and a `limit` of 1–100
(default 50). Send `next_after` as `after` for the next page.

`GET /v1/event-logs/{log_id}/temporal-profile?zeta=&min_occurrences=&case_type=` plus the case-filter
parameters. Agents with access to a shared log get the pair figures, without case IDs, through
`get_process_temporal_profile`.

## Decision rules (decision mining)

Decision mining finds the points where cases take different routes and learns which recorded
data goes with each route. ProM's decision miner, PM4Py, Celonis and SAP Signavio offer the same
analysis. The numeric rules use the full precision of the scored split.
A rule condition with `includes_missing` also matches an absent attribute.
A numeric condition with `includes_non_numeric` also matches unusable numbers.

1. **Decision points.** These are activities after which cases continue to at least two different
   next activities, or end. Each branch must be seen at least 5 times. Completion
   normally appears as `(end)`. If a literal activity uses that label, the result
   picks a distinct `terminal_label`. Branches and rules flag completion using
   `next_is_terminal`. Choose **Review branches**, then **Inspect cases** to see which
   cases contributed to an observed branch and how many times it occurred in each case.
   Open a case to review its event history.
2. **Data known at the decision.** The rules use two kinds of data:
   - the case's attributes (`case.*`);
   - the latest non-empty value of every event attribute recorded on the case at or before the
     decision event (`event.*`).

   Use **Timed event attributes only** to exclude untimed case attributes.
   This setting keeps event values recorded at or before the decision. The
   combined setting also uses case attributes, which can have been set later.
   Pass `include_case_attributes=false` to the decision-rules endpoint for
   the event-only mode.
3. **Rules.** A CART decision tree (Gini impurity, depth 1–4, default 3) is learned on about 70% of
   the cases and scored on the rest.
   - The split is by a hash of the case id, so it is repeatable and keeps each case on one side.
   - Numeric attributes are split at the best boundary between observed values. Text attributes
     are split on their 30 most common values.
   - Each leaf is a rule. It shows its conditions, the usual next activity, its confidence and the
     number of training occurrences.
   - **Held-out results by branch** lists recorded and predicted occurrences,
     correct predictions, precision, and recall. Precision is undefined without
     predictions for that branch. Recall is undefined without recorded holdout
     occurrences. **Held-out branch mix-ups** shows which recorded branches were
     predicted as another branch and how often.

The result also gives:
- the held-out accuracy, next to the accuracy of always predicting the most common branch;
- `beats_baseline`;
- a confusion table;
- the share of the tree's impurity reduction due to each attribute.

Rules are associations in this log, not the business rule or its cause, and authorize nothing.
At most 200,000 occurrences of the activity are used, and the result says when it was capped. The
global case filter applies.

- `GET /v1/event-logs/{log_id}/decision-points?case_type=` plus the case-filter parameters.
- `GET /v1/event-logs/{log_id}/decision-rules?decision_activity=&max_depth=&case_type=` plus the
  case-filter parameters. The case filter's own `activity` parameter still selects cases that
  contain an activity.
- `POST /v1/event-logs/{log_id}/decision-points/cases` uses the case notion and filter from
  the decision points. Send `{"activity":"Check","next_activity":"Review"}` for a continuation
  or `{"activity":"Check","ends_here":true}` for completion. It reports all observed
  occurrences, including repeats, and pages through distinct cases in literal case-key order.
  The optional `limit` is 1–100 (default 50). Send the returned `next_after` as `after` to continue.

These routes need a dashboard session. Agents can read the aggregates
through `get_process_decision_rules` (see PROCESS_INTELLIGENCE_FOR_AGENTS.md).

### Excel event-log export

Dashboard owners can download a saved log as an Excel workbook (`GET /v1/event-logs/{log_id}/events.xlsx`). The workbook preserves case IDs, activities, timestamps, and resources as literal text cells, with a frozen header and filter. It is a CSV-equivalent view rather than an OCEL relationship export.

### Rework and loop case evidence

The local rework report shows the total number of repeated activities and recorded loop patterns, with an adjustable display limit of 20, 50 or 100. JSON and CSV downloads contain the displayed results and the JSON includes the selected cohort and analysis settings. The report offers explicit case-evidence links for repeated activities and exact closed activity loops. Selectors respect the active case cohort and retain literal event order; timestamp ties do not establish business order.
