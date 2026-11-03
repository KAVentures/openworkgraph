# Selected ServiceNow incremental read → journal → existing OWG event log

This is an **operator-invoked**, consent-gated continuation after the
[verified ServiceNow bootstrap](SERVICENOW_OWG_BOOTSTRAP.md). It adds a
single-command path for a *selected set of 1–8 incident IDs* from a real
read-only ServiceNow Table API GET to the existing private SQLite journal
and the **already existing** local OWG event log. It does not perform
background synchronization or retrieve a complete incident history.

## Operator procedure

1. Start the local OWG server and sign in with a valid dashboard session.
   Confirm that the selected source's first import already exists, and
   inspect the event log's source namespace and event count.
2. Retain the exact same private owner-only journal, source grant, selected
   incident IDs, tenant label and declared IANA timezone used at bootstrap.
   The local grant must still be enabled and consented. This marker is **not**
   independent proof of ServiceNow enterprise authorization.
3. Obtain an authorized read-only ServiceNow bearer and local OWG dashboard
   session. Set them as environment variables, not CLI arguments or logs:
   **OWG_SERVICENOW_TOKEN** and **OWG_DASHBOARD_SESSION**.
4. From the repository root, run (replace example identifiers, paths
   and log ID with the exact reviewed values):

       python -m server.servicenow_owg_incremental --instance example.service-now.com --tenant selected-team --incident-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --grant-file /private/owg/grant.json --timezone UTC --journal /private/owg/selected.sqlite --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef --port 8765 --consent-selected-read --consent-local-journal-update --consent-local-append

   Optional **--since '2026-10-30 00:00:00'** is a **manually selected**
   provider-local inclusive lower bound, not a persisted or auto-advanced
   watermark. Use an overlap window when reviewing late source updates;
   replayed source revision IDs are deduplicated. The provider may omit
   historical events or deleted records.
5. Inspect the counts-only JSON result and OWG's Event logs / append history.
   A **verified** receipt means the local append response, source namespace
   and independent log event-count readback agreed. It **does not** verify
   completeness of ServiceNow data or business outcomes.

## Safety and recovery

Before any provider request, OWG validates the exact selected grant, existing
journal scope and contents, current authenticated local session, source
namespace, existing log ID, and all three separate consents. A private
exclusive lock refuses concurrent invocations against the same journal.

The provider request uses the existing one-page, exact-host, no-redirect,
bounded HTTPS reader. The existing projection retains only selected incident
state, revision and update timestamp with hashed event/case IDs. The existing
SQLite journal transaction commits new revisions before the local append
POST. The existing OWG authenticated REST append skips duplicate source IDs.

After POST, the command checks the current grant and independently reads
the log list to verify the exact source scope and event-count change. If
anything after attempting POST fails, the command returns
**status=unverified**, **possible_local_append=true**, exit code 3. **Do not
automatically retry**: inspect OWG's append history and the durable journal
first. A source failure before POST cannot change the OWG log, but a
successfully committed journal update can survive a later pre-POST refusal.

A selected GET returning zero rows is reported as
**status=no_selected_snapshots** without an OWG append. This does **not**
mean all selected incidents are absent or deleted. The connector never
deletes OWG evidence, changes retention, advances checkpoints, writes to
ServiceNow, schedules requests or authorizes agent actions.

The journal and event log remain local business metadata subject to the
organization's retention, backup, access and deletion policies. Revoking
the local grant blocks future reads at the checked boundaries, but cannot
undo already committed data or provide an atomic fence against independent
provider-side revocation.

## Acceptance limits

The committed integration tests exercise the production ServiceNow reader
with disposable provider-shaped HTTP, actual private SQLite journal and
authenticated local FastAPI/DuckDB import/append/readback routes, including
revision replay, source-scope refusal, consent, revocation, uncertain POST
and private CLI output. This is **not** customer sandbox acceptance.

Before merge, all six exact-head GitHub Actions workflows and applicable
native Ubuntu/macOS/Windows pytest, browser, process collaboration, scale,
accessibility, starter and packaging/release checks must pass, with resolved
reviews and conflict-free current main. Real provider sandbox acceptance,
managed credential rotation, late-event completeness, deletion/retention
policy and unattended schedules remain open under #326 and #370 R3.
