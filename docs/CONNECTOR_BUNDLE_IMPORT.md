# Verified local connector bundle import

Enterprise delivery #1572. Follow-up to consolidated integration #1589.
This workflow imports ordinary Gateway connector event-bundle JSON directly
into the local mining workspace, without manual CSV conversion or mapping.

## Operator workflow

1. Download an ordinary connector event JSON bundle from the Gateway using
   its existing owner-approved export. Obtain it through an authorized
   channel; a file's internal digest is not a signature.
2. Open **Enterprise connector operations → Import published connector
   evidence** in the local dashboard. Choose the file and either create a
   new mining log or choose a compatible prior verified-import log.
3. Approve local inspection. Preview validates the original manifests
   against all canonical source rows, including exact counts, event and
   case identities, observed-time windows, and coded activity format.
   Review the source job, provider, publication grant ID, manifest digest,
   profile revision, label mode, event-time range and source change
   checkpoint. Preview does not persist event rows.
4. Separately approve the file origin and local retention. Execution
   verifies the exact raw-file SHA-256 and canonical bundle SHA-256 again.
   The original file bytes are sent to the local server, not a Gateway or
   provider. No source credentials or network provider reads are needed.
5. The existing supplied-source-key mining ingestion path uses a fixed
   mapping. Different source jobs remain different case namespaces even
   when a native case ID or event ID happens to match.
6. Review the retained counts and import receipts. Use the normal mining
   dashboard to analyze the resulting log.

## Incremental imports and integrity

New-log requests are idempotent while their request receipt remains
retained. A changed name, target, file or review identity cannot reuse a
committed request ID. Append requires a reviewed current receipt revision,
the exact original set of source job IDs, provider profile IDs/revisions/
digests and activity modes. A new source set, changed profile or label
mode requires a new log. Reusing the same job/grant with a different
manifest is rejected. This is a conservative offline association, not
independent proof of source identity.

A previously retained exact canonical bundle can be replayed into the
same log without another insertion. Later overlapping windows skip exact
duplicate source keys and retain new keys; conflicting existing event
payloads abort the transaction. The import verifies the projected rows
inside the same transaction, including activity, time, source key and
case membership. No silently dropped or changed row may be counted as a
successful verified import. The receipt is written in that transaction.
Existing workspace auxiliary quality metadata is generated separately;
the atomic receipt records the import, not a guarantee of auxiliary
metadata success.

Generic log appends remain available through the ordinary workspace.
A log whose event/object counts or append batch history changed outside
this workflow cannot receive another controlled bundle append.
Import receipts describe the original import operations and do not
certify the current log. Deleting the log removes the receipts in the
same deletion transaction. Previously downloaded files and backups have
their own retention policies.

## Trust boundaries and format

Only ordinary openworkgraph.connector-event-bundle.v1 JSON with the
existing fixed source-key mapping is supported. Linked-case exports,
quality-annotated bundles and other formats are rejected rather than
dropping their semantics. Maximum input is 16 MiB UTF-8 JSON, 10 sources,
5,000 events per source and 20,000 events total. Strict field validation,
duplicate JSON key rejection, nonfinite value rejection and canonical
SHA-256 checks apply. No ZIP, URL retrieval, executable imports or
unrecognized mapping are accepted. Large JSON exports may exceed the
byte cap even within the event cap; select smaller disclosed samples.

The hashes are unkeyed self-consistency checks. A malicious origin could
replace both rows and hashes; the importer does not authenticate the
Gateway, check whether grants remain live, independently verify provider
completeness or establish cross-source business identity. An imported SAP
business date remains a date encoded at midnight, not a verified action
time. Revoking a publication does not erase its previously downloaded
local copy. Store only data you are authorized to retain.

The local dashboard session is required for all API operations. The
preview response contains source manifests and aggregate counts, not
event rows or activity labels. Receipt history is bounded to 100 distinct
committed bundles per log; a new log is required beyond that limit.
Receipt data includes declared source IDs and digests, not raw source
payloads or credentials.

## API

Under /v1/enterprise-connectors/bundle-import:

- GET: list verified-import logs and provenance summary.
- GET /{log_id}/receipts: view retained import receipts.
- POST /preview?target=...: raw JSON bytes with
  X-OWG-Bundle-Inspection-Confirmed: true.
- POST /retain?target=...&expected_revision=... (or name=... for new log):
  same raw JSON bytes, X-OWG-Bundle-File-SHA256,
  X-OWG-Bundle-Canonical-SHA256, X-OWG-Bundle-Request-ID (32 hex chars),
  X-OWG-Local-Storage-Confirmed: true and
  X-OWG-Bundle-Origin-Reviewed: true.

All POST requests use Content-Type: application/json and the existing
OWG-Session authorization. The server bounds raw bytes during streaming.
There is no upload staging cache between preview and retain.

## Validation

The exact committed pure verifier (blob
cf9dde20730e3e01e5e3c079552369661003c621) and its two exact dependency
blobs were verified locally. 500 randomized valid-bundle permutation
checks and 500 altered-row rejection checks passed, as did malformed JSON
and strict boolean checks. A synthetic full 20,000-event validation took
about 0.81 seconds; this measures parsing and consistency checking only,
not local database import throughput.

Regression fixtures use the existing actual SQLite/PostgreSQL Gateway
publication store and retained mining workspace. They cover tampered
rows/manifests, rejected extra semantics, exact receipt replay, scope
and grant conflicts, stale append revisions, atomic rollback, generic
append drift and log deletion. Chromium exercises file selection, exact
preview, retention, append and receipt replay. Focused draft acceptance
is registered. Direct git checkout failed DNS and DuckDB is not installed
in the authoring environment, so integrated tests remain pending CI.
Land after #1589 acceptance and reconcile overlapping follow-ups.
