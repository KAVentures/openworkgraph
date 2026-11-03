# SAP change-document export import

The offline converter turns an explicitly selected SAP **change-document** export
(tables `CDHDR`, optionally `CDPOS`) into an event log usable in the existing OWG
CSV import workspace. It makes no SAP or network requests, stores no credentials,
runs no background extraction and performs no business writes. It is an export
integration, not a continuous connector: live OData extraction remains open in
the source-connector workstream (#326 B).

## Export

Export `CDHDR` with at least `OBJECTCLAS`, `OBJECTID`, `CHANGENR`, `UDATE`,
`UTIME`, `TCODE` and `CHANGE_IND` using your authorized SAP tooling (for example
SE16N or a data-services job), as UTF-8 CSV with unique headers. Other columns
such as `USERNAME` may be present; they are not copied.

To name activities after a status field, also export the matching `CDPOS` rows
with `OBJECTCLAS`, `OBJECTID`, `CHANGENR`, `TABNAME`, `FNAME` and `VALUE_NEW`, and
declare the field as `TABNAME.FNAME`. Only that field's new value is used;
`VALUE_OLD` and every other field's values are never copied.

## Convert

SAP stores `UDATE`/`UTIME` in the system's time zone without an offset, so the
offset must be declared. Select the exact literal object IDs to analyze:

```sh
python scripts/sap_change_documents.py cdhdr.csv \
  --cdpos cdpos.csv --status-field VBUK.GBSTK \
  --namespace erp-prod --object-class VERKBELEG \
  --object 0000012345 --object 0000012346 \
  --utc-offset +01:00 \
  --output selected-sap-events.csv
```

`--cdpos` and `--status-field` go together. The output path must be new. The
JSON report printed to stdout contains only counts and fixed projection
metadata. Limits: 16 MiB and 200,000 rows per file, 32 selected objects.

Each change document (`CHANGENR`) of a selected object becomes one event:

- `Status: <value>` when the declared status field changes in that document;
- otherwise `Created (<TCODE>)` for change indicator `I` and `Changed (<TCODE>)`
  for `U`, or `Created`/`Changed` without a transaction code.

The whole export is refused, not partly converted, when a selected row has a
malformed date or time, an unsupported change indicator (`D` and others are not
projected), an invalid change number, conflicting header rows for one change
number, or two different status values in one change document. Rows of other
object classes and unselected objects are excluded and counted.

## Import

In **Event logs → Import CSV**, map `case_id`, `activity` and `timestamp`. For
later exports with supplied-source-ID deduplication, map `source_event_id` and
keep the same namespace. The namespace must stay stable for one SAP system and
client; use different namespaces for distinct systems. It is a declaration, not
verified system identity. Object and change keys are namespace-scoped SHA-256
values. They prevent accidental collisions but are not anonymization: status
labels and transaction codes still describe business activity, so review the
output before sharing.

## Incremental exports

SAP assigns change numbers from one ascending number range per system, so a
change number works as a checkpoint. Every report includes `next_after_change`,
the highest change number it projected. Pass it back for the next export:

```sh
python scripts/sap_change_documents.py cdhdr-later.csv \
  --namespace erp-prod --object-class VERKBELEG --object 0000012345 \
  --utc-offset +01:00 --after-change 0000001005 \
  --output selected-sap-events-2.csv
```

Only change documents with a higher change number are projected; earlier ones
are counted as `before_checkpoint_rows`. Load the result with **Event logs →
Append** to the same log. Event keys are stable, so overlapping or repeated
increments are skipped as duplicates rather than doubled. Keep the namespace,
object class, selected objects, status field and UTC offset the same across
increments. An export with nothing after the checkpoint is refused rather than
producing an empty file. The checkpoint is an SAP-internal change number, not a
business value, but it does reveal how many changes the system has recorded.

## Limits

Change documents only cover fields with change-document logging enabled, and
retention depends on the system. Missing objects or events do not prove
inactivity, completion or a complete process. The declared offset is applied to
every row; changes across a daylight-saving transition are not adjusted. Ties
are disclosed in the report and ordered by event key, not known business
chronology. No automation or execution approval follows from this import.
