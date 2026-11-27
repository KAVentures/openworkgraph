# Native SQL event feeds

Enterprise delivery program #1572. This increment is stacked on the
shared enterprise feed infrastructure (#1573) and native Google Workspace
adapter (#1574). It adds native PostgreSQL and SQLite ingestion; no customer
HTTP shim or arbitrary SQL execution is required. All retained events,
checkpoints, leases and scheduling reuse the common local implementation.

## Deployment-approved source catalog

The deployment owner defines an immutable source catalog in the OWG process
environment, not in an agent prompt or editable dashboard SQL textbox:

```json
{
  "orders": {
    "engine": "postgresql",
    "schema_name": "owg_reporting",
    "relation": "order_events",
    "database": "erp",
    "role": "owg_reader",
    "dsn_env": "OWG_SQL_DSN_ORDERS",
    "mapping": {
      "event_id": "event_id",
      "case_id": "order_id",
      "activity": "activity",
      "event_time": "occurred_at",
      "change_time": "updated_at",
      "resource": "operator_id"
    }
  },
  "nightly_export": {
    "engine": "sqlite",
    "path": "/approved-exports/audit.sqlite",
    "relation": "event_history",
    "mapping": {
      "event_id": "event_id",
      "case_id": "case_id",
      "activity": "activity",
      "event_time": "event_us",
      "change_time": "change_us"
    }
  }
}
```

Set this JSON object as `OWG_SQL_SOURCE_CATALOG`. It allows at most 50
definitions. Identifiers are strict quoted ASCII identifiers; there are no
custom queries, joins, arbitrary functions, wildcards, extra filters or
user-supplied SQL. A customer administrator can expose a reviewed database
view for joins, field normalization and row-level restrictions. The source
database and deployment administrator remain trusted: this is not a sandbox
for hostile database views or functions.

The SQL catalog view displays alias, engine, exact relation and mapped
columns, timestamp contract, driver availability and a definition SHA-256.
It does not connect to either database or expose the DSN, credential,
database path or password. The full catalog is not part of the shared
operational JSON export. Exact definition approval is required when creating
a profile; changing the deployment definition invalidates the old profile.

## PostgreSQL

Use the repository's existing `gateway` optional dependency group (psycopg)
in the Python deployment that runs the local API, e.g.
`python -m pip install -e ".[gateway]"` from a checked-out repository.
No new dependency or lockfile change is introduced. If psycopg is not
available, the catalog marks the PostgreSQL driver unavailable. Native
desktop installer inclusion of this extra remains a separate packaging task.

Set the deployment's secret reference, for example:

    OWG_SQL_DSN_ORDERS=host=db.example.com port=5432 dbname=erp user=owg_reader password=... sslmode=verify-full sslrootcert=/approved-ca/ca.pem

Do not put the password into the source catalog or application UI. A DSN must
select one exact host, database and role, password, verify-full TLS and
explicit trust root. Service files, alternate hosts, sockets, user-supplied
connection options and weaker TLS modes are refused. Credentials are bound
to the exact profile; rotation or a source change currently requires a new
profile. The UI never initiates a database connection to test a credential.

A database administrator should grant only SELECT on the specific reporting
relation, USAGE on its schema and CONNECT on its database. Use a dedicated
non-superuser role and enforce provider-side least privilege independently.
The connector explicitly starts a repeatable-read read-only transaction,
sets bounded statement/lock/idle timeouts and checks current database,
session/current user, transaction isolation/read-only state and active TLS.
A named server cursor fetches bounded batches from one source snapshot.

The mapped event and change time columns must be `timestamptz`.
Identifiers may be PostgreSQL text, varchar, integer or UUID; activity
labels must be text or varchar. The optional resource ID can be NULL.
Mapped columns must have native supported types, not arbitrary domains.
The connector refuses a relation with NULL change timestamps. Selected
column metadata, relation OID/kind and view definition are hashed and pinned
after the first successful window, including an empty one. Subsequent
changes refuse retention; unrelated underlying view behavior can still
change without altering the visible definition. No full schema or raw
view SQL is retained.

## SQLite

Choose a canonical absolute path to a regular local SQLite file outside
OWG's retained data directory. No path is entered through the dashboard.
The profile binds the file identity (device/inode where supported), exact
catalog definition and mapping. File replacement requires a new profile.
The connector uses SQLite URI mode=ro, query_only, trusted_schema=OFF and
a single read transaction with a progress deadline. It opens no attached
database and accepts only a STRICT ordinary table, not a view or virtual
table. Use a reviewed materialized export from the source system.

Example table:

```sql
CREATE TABLE event_history (
  event_id TEXT NOT NULL,
  case_id TEXT NOT NULL,
  activity TEXT NOT NULL,
  event_us INTEGER NOT NULL,
  change_us INTEGER NOT NULL
) STRICT;
```

Both timestamps are exact integer UTC microseconds since the Unix epoch.
Change time must be NOT NULL. IDs may be TEXT or INTEGER; labels TEXT.
Selected column metadata is pinned after a successful window. A source
table rebuilt with identical selected metadata in the same file cannot
always be distinguished; no independent source completeness claim is made.

## Operator workflow

1. Open Business system sign-in → Enterprise connector operations.
2. Select **Create native SQL event feed**, choose an approved catalog source,
   review exact relation/mapping and definition pin, set a first change time
   within 366 days, advancing window and overlap.
3. Separately approve configuration and activity-label retention. Saving
   does not open a database or read its records.
4. Confirm one bounded source read and local retention, then inspect the
   retained event log. Recurring synchronization requires separate consent
   and the exact reviewed profile revision through the existing scheduler.
5. The first nonempty window creates a retained log. Later windows append
   exact source event IDs. Empty complete windows advance checkpoint and
   establish the schema pin without creating a fabricated log.

The query uses parameter-bound inclusive start/exclusive end and LIMIT.
Rows are fetched from one snapshot, so tied timestamps do not disappear
between offset or keyset pages. The event, case and resource identifiers
are profile-local HMAC pseudonyms; activity labels and event timestamps
are retained. Unmapped fields and raw source rows are not persisted.
Text fields are bounded in SQL and overlength or invalid characters refuse
the batch instead of being silently truncated.

Bounds: at most 20 fetch batches, 500 rows per batch, 10,000 received
rows and 16 MiB projected bytes, plus a nominal 75-second read budget.
The inherited lease is 120 seconds; individual DB operations are separately
bounded. An overflowing window fails explicitly rather than advancing
a partial checkpoint. Configure a narrower reviewed profile/window where
needed. The source's change timestamp must reflect immutable event history
or updated records. Late records outside the approved overlap may be missed.
Conflicting retained payloads refuse the entire append; deletes and
corrections require a separate reviewed workflow. Source completeness,
actual business cases and cross-provider identity are not independently
verified.

The shared transaction commits the checkpoint, source schema pin and
retained events together. Profile/source drift and revocation are checked
before fetch batches and at retention. Deleting the retained dataset
invalidates the source profile and checkpoint; removing the profile retains
the log. Recurring jobs pause on changed source/schema approval rather than
silently switching databases. Live health is unknown in passive inventory.

## API and acceptance

- GET /v1/enterprise-connectors/sql/catalog: passive deployment source list.
- POST /v1/enterprise-connectors/sql: exact source digest, profile and
  explicit approvals. Uses existing dashboard-session-only access.
- Common run/state/remove endpoints and scheduler are unchanged; SQL is a
  provider in the shared enterprise feed runtime.

Regression coverage: `tests/test_enterprise_sql_feed.py` exercises real
SQLite source files and retained DuckDB, bounded batches, same-time events,
overlap, duplicate keys, credential/source replacement, malformed mapping,
schema drift, privacy, revoked profiles, dataset deletion and authenticated
HTTP. A dedicated CI PostgreSQL service uses an ephemeral TLS certificate,
a restricted reporting role and a real view to test TLS, transaction state,
native types and view-definition drift. The Chromium fixture configures and
runs an actual local SQLite snapshot through the operator console.

This does not yet include SQL Server, MySQL, Snowflake, Databricks,
distributed Gateway jobs, in-place identity-verified credential rotation,
CDC/change streams, dynamic source mapping edits or independently measured
enterprise throughput. Full CI and customer acceptance remain required.
