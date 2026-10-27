# Supplied source-event identities for CSV logs

The local event-log workspace supports an optional source-ID mapping on CSV import and append. It stores keys beside the existing DuckDB events, objects and event-object links. It does not create another event store. Existing CSV mappings retain their payload-based append deduplication and existing transform/content digest definitions.

Choose **Supplied source-event IDs** in the import card, select an event-ID column, and supply either a source namespace or its column. Review the case namespace and selected retained attributes, then confirm local storage. Changing identity settings clears that confirmation. This is local dashboard access; an AI bearer token cannot import, append, inspect or export these business logs.

## Identity and case rules

An event key is the exact pair `(source_namespace, source_event_id)`. IDs are literal, case-sensitive, not trimmed or truncated, nonblank, control-free strings of at most 200 characters. Namespace values are case-sensitive, 1–40 characters matching `[A-Za-z0-9][A-Za-z0-9_.-]{0,39}`. A log holds at most twenty source namespaces, each bound to one case namespace. A later append cannot change that binding.

- Distinct keys retain distinct events even with identical activity, time, resource, attributes and case.
- Repeated keys with the same retained payload are skipped, within a file and across appends.
- Reusing a key with changed activity, time, resource, retained attributes or case refuses the entire batch. The lookup has no overlap-time-window restriction. Corrections need a separately reviewed update policy; they are not silently applied or duplicated.
- Equality concerns the normalized, **retained** data. Unselected columns cannot detect a changed payload. Extra-column normalization/bounds are the existing CSV rules; attribute JSON compares object content independent of key order. Timestamps compare as UTC instants; an explicit existing naive-time offset decision remains supported.
- IDs do not establish causal order. Tied timestamps retain the existing local event-ID tie breaker.

By default case namespaces inherit source namespaces. Cases use the separate `source_case` notion and object key `src:<case_namespace>:<literal_case_id>`. The complete namespaced key must fit 200 characters; it is never truncated. Choosing a common case namespace explicitly joins equal literal case keys across sources. It is the caller's exact-key assertion, not a timestamp/label join or proof that records belong to one business case. A collision with an existing object of another type refuses the batch.

Both source and case identities are caller-declared. Uploaded IDs do not authenticate a provider, establish business intent, prove historical completeness, authorize execution or verify an outcome. The design uses the identity principle in [CloudEvents 1.0.2 source/id](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md#id); this is not a CloudEvents implementation.

## API mapping and complete batches

Authenticated `POST /v1/event-logs/import` and `POST /v1/event-logs/{log_id}/append` accept the existing CSV mapping plus:

| Query parameter | Meaning |
| --- | --- |
| `event_id_column` | Required supplied-event-ID column when opting in |
| `source_namespace` | Constant source namespace; choose this or the column |
| `source_namespace_column` | Per-row source namespace column |
| `case_namespace` | Optional constant case namespace; choose this or the column |
| `case_namespace_column` | Optional per-row case namespace column; defaults to source when neither is set |
| `attributes_json_column` | Optional retained string-attribute JSON object column; incompatible with `attribute_column` selections |

The existing `X-OWG-Local-Storage-Confirmed: true` header is required. Import format remains `csv`; identity mapping on XES/OCEL is refused. Every row must have valid case, activity, timestamp and source identity. `keep_rejected=true` is explicitly incompatible: the legacy quarantine format does not retain source keys. Invalid rows, conflicting keys, namespace limits and integrity failures refuse the complete batch.

Events, links, objects, source index, append history and log counts share one transaction. Concurrent local appends are serialized by the existing workspace lock. Failure after source-index insertion rolls back the batch too. Deleting a log deletes its index. Legacy/OCEL appends cannot add unkeyed events into a managed `source_case` notion. Other case notions can coexist; source-ID export selects only the declared source-case portion.

Quality reports remain best effort **after** data commit, as before. A diagnostics failure does not undo imported events; the quality endpoint discloses missing reports. Source-mode reports include the matching basis and repeated-ID count. The content-duplicate diagnostic still counts equal case/activity/time/resource payloads; it is not a source-key duplicate count. The separate `source_identity_map_sha256` hashes the declared source/event/case associations without exposing their values in counts-only diagnostics. Existing content and transform digest definitions remain unchanged for ordinary mappings. Digests are reproducibility checks, not signatures or independently verified identities.

## Round-trip export

Choose **Source-ID CSV (source_case only)**, or request `GET /v1/event-logs/{log_id}/export?format=sourcecsv&case_type=source_case`. It writes a normal `.csv` file with columns:

```text
source_namespace,source_event_id,case_namespace,case_id,activity,timestamp,resource,attributes
```

It preserves supplied IDs, namespaces, the raw case key, UTC timestamps, resource and retained attributes. It refuses a missing/inconsistent source index or ambiguous case links before writing output. Reimport as `format=csv`, map case/activity/timestamp/resource normally, and use `source_event_id`, `source_namespace`, `case_namespace` and `attributes` for the four corresponding identity/JSON column mappings. The UI recognizes those column names for selection but leaves the source-ID option explicit. A raw case key already starting with `src:` round-trips without an extra prefix.

Ordinary CSV, XES and OCEL exports retain their existing contracts and **omit these source-key associations**. They are not source-ID round trips. Shared coded-case disclosure still omits original source/case/person labels, resources, attributes and absolute dates. Source IDs are not automatically synchronized or included in that disclosure.

## Verification and limits

`tests/test_event_log_source_identity.py` covers exact keys, payload conflicts, within-file retries, namespace isolation/common-case assertions, limits, malformed mappings and attributes, literal Unicode/quoted headers, index gaps, injected transaction failure, concurrent retries, HTTP authentication/storage consent, deletion, default behavior and round trips. `tests/browser/event_log_source_identity.cjs` exercises the real local dashboard, export/reimport, WCAG and mobile layout. The pre-existing event-log browser and ingestion/quality/quarantine suites remain regression gates.

Run the isolated synthetic oracle with the repository venv:

```sh
python scripts/benchmark_source_event_identities.py --events 100000
python scripts/benchmark_source_event_identities.py --events 1000000
```

It creates four declared namespaces, retains distinct keys with equal payloads, retries the complete original file, adds another half-file of distinct IDs with matching payloads, refuses a conflicting timestamp outside the old overlap window, and checks every exported row against an independent integer-key oracle before reimport. It reports timings, process RSS and DuckDB settings. These fixtures do not certify provider extraction, a tenant service SLA or enterprise production scale. See the PR's recorded hardware/results for the tested environment.

Live provider extraction/checkpoints, authenticated source identifiers, durable shared ingestion jobs, correction/version policies, native XES/OCEL source identity mappings, identity-preserving quarantine, database/host recovery and actual company acceptance remain separate work. No capture, sharing, customer credentials, paid model or business-system action is enabled by this feature.
