# Supplied OCEL object relationships

OCEL JSON and XML imports retain directed object-to-object assertions separately
from event-to-object links and shared-event associations. An assertion is the
literal tuple `(source object ID, target object ID, qualifier)`. Duplicate tuples
form one retained assertion; different qualifiers, opposite directions, cycles
and self-links remain distinct. Empty and whitespace qualifiers remain distinct.
Forward references and objects without linked events are supported.

These are caller-supplied, static assertions. They do not establish a business
fact, case membership, causal order, a validity period or execution permission.
An order-to-item assertion does not add the item's events to the order's case.
Existing object-centric lifecycle and shared-event maps retain their semantics.

## Local explorer

Analyse a retained OCEL log and open **Supplied object relationships**. Filter
literal object ID, source/target type, incoming/outgoing direction, or an exact
qualifier (including empty). Select an object in the table or diagram to inspect
direct neighbors and its current linked log events. Objects without events stay
visible without fabricated lifecycle steps. The main analysis case notion stays
unchanged. Group buttons select the corresponding type/qualifier filters.

The table pages at 1–200 assertions. Cursors bind the log, filters and append
revision; after an append, restart at page one. Group summaries show at most 200
groups with an explicit total/truncation flag. A diagram shows at most 12 neighbor
objects and 24 links from the current page, with its display limits stated. The
table retains every assertion on that page. Inspection is one hop, not transitive
closure or a derived case correlation.

Authenticated local routes:

| Route | Result |
| --- | --- |
| `GET /v1/event-logs/{log_id}/object-relationships` | Exact retained count and bounded type/qualifier groups. |
| `POST /v1/event-logs/{log_id}/object-relationships/query` | Literal `object_id`, `source_type`, `target_type`, `qualifier`, `direction` (`both`, `incoming`, `outgoing`), `limit`, optional returned `cursor`. |

Both require the local dashboard session; an AI API bearer is insufficient.
Query JSON is limited to 64 KiB; duplicate keys, non-finite numbers, unknown fields
and unbounded filters are refused. Import/append still require explicit local
storage consent. Reading relationships adds no sharing or execution authority.

## Storage, append and export

Assertions reside in the existing DuckDB workspace, scoped to their log. Import
validates every endpoint after reading the object declarations. Missing or
ambiguous endpoints, malformed links, unsupported relationship fields, control
characters or limits refuse the whole batch. Limits are 10,000 input links per
object and 1,000,000 input links per batch, with the same per-object/whole-log
limits on the distinct retained union after append. IDs are nonblank and at most
200 characters; qualifiers are strings of at most 80 characters. Values are not
silently trimmed or truncated. Existing reader/file-size limits still apply.

Append unions assertions in the same transaction as objects, events, timed
attributes and append history. A batch with no new events may add assertions.
Retrying an assertion does not inflate counts. Omission does not remove an old
assertion: there is no relationship replacement, deletion or historical validity
API. Deleting the log removes its assertions. A corrupt existing index refuses
append and OCEL export before partial output rather than silently repairing it.
Post-commit quality diagnostics remain best effort, as for other imports.

OCEL JSON/XML exports preserve the retained assertion set. Plain CSV, Source-ID
CSV and XES exports omit it; the explorer discloses this loss. They are not
relationship interchange formats. Optional `object_relationships` metadata and a
separate `relationships_sha256` in quality reports describe the whole retained
assertion set. Existing event-content and transform hash definitions are
unchanged. Edge-free logs retain their existing response/output shapes.

Older imports that discarded object relationships cannot be backfilled from
events. Reimport the original authorized OCEL file. Assertions are not added to
coded shared analyses or shared-case disclosures. Typed attributes, event identity
and object-attribute history keep their existing independent format limits.

## Standard and interoperability scope

The pinned reference is the original [OCEL 2.0 specification, arXiv v1](https://arxiv.org/pdf/2403.01975v1),
version 2.0 dated 2023-10-16: section 6.6 defines directed qualified object pairs;
section 8.2 defines JSON `objects[].relationships[{objectId, qualifier}]`.
XML uses the section 7.1 example's canonical
`<object><objects><relationship object-id="..." qualifier="..."/></objects></object>`.
An omitted XML qualifier means empty. Objects precede events as in the original
printed root schema; event-first streaming XML is not supported.

The original printed XML child schema and worked example disagree (`object`
versus `relationship`). OWG follows the worked example, also emitted/read by
PM4Py 2.7.17, and does not claim blanket XSD conformance. The website's old 2.0
PDF URL now serves 2.1 pre-release material, so it is not the normative pin for
this feature. Unknown relationship semantics are explicitly refused, not dropped.

Independent PM4Py JSON/XML checks cover observed endpoints. That library prunes
objects without events; OWG's separate exact tuple-set, endpoint, event-history
and digest oracles cover those objects instead. Synthetic fixtures and scale
checks establish bounded behavior, not live provider, customer, conformance or
process reconstruction certification.
