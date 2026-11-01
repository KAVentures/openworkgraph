# ServiceNow journal to authorized local OWG event-log append

Worker E / #326 Section B / #264 Stage 3. This is a working,
operator-invoked, **disabled-by-default** local append for the ServiceNow
selected-incident journal. It does **not** contact ServiceNow, create a
background scheduler, obtain a provider credential, infer source history,
write to ServiceNow, or upload to a cloud service. It uses OWG's existing
authenticated, transactional event-log append API.

## Prerequisites

1. Run OWG locally and create a saved event log through **Event logs → Import
   CSV**, initially using a selected ServiceNow CSV export from
   [the journal workflow](SERVICENOW_INCIDENT_JOURNAL.md), with source-event
   identity mapping. The target log must exist; this command **never creates**
   a new log. Source-keyed append against an incompatible legacy log is refused.
2. Retain the original private owner-only SQLite journal, exact host, local
   tenant, selected incident IDs, declared IANA timezone and matching enabled
   local grant. A changed scope is refused before any HTTP call. The grant is
   checked again after reading the journal and after the append response.
3. Obtain a **live OWG local dashboard session** through the ordinary local
   dashboard authentication flow. Supply the session secret only in the
   process environment as OWG_DASHBOARD_SESSION. Do not put it in a CLI
   argument, URL, log, shell history, repository or exported CSV. This is not
   an MCP bearer token or an IdP/ServiceNow credential.
4. Confirm the target log ID using OWG's local Event logs view. The command
   requires both the selected target and an exact repeated confirmation,
   plus two independent explicit consent flags.

Example, with a private owner-only journal directory and local OWG listening
on literal IPv4 loopback port 8765:

~~~sh
python -m server.servicenow_owg_append \
  --instance example.service-now.com --tenant selected-team \
  --incident-id 0123456789abcdef0123456789abcdef \
  --grant-file /private/snow/grant.json --timezone UTC \
  --journal /private/snow/snapshots.sqlite \
  --log-id log_0123456789abcdef \
  --confirm-log-id log_0123456789abcdef \
  --port 8765 \
  --consent-local-journal-read --consent-local-append
~~~

**This command modifies the explicitly selected local OWG event log.**
It never runs unless invoked with both consent flags, a current matching
local grant and a live dashboard session. The REST request goes only to
literal 127.0.0.1 and the selected port; no DNS, ambient proxies, redirects
or remote host overrides are allowed. OWG itself requires dashboard session
authentication and explicit local-storage confirmation. The CLI does not
bypass the existing dashboard permissions.

The command verifies the journal's scope and owner-only file/directory
properties, reads its bounded 4,096 snapshots, validates the CSV fields and
UTC timestamps, and sends at most 2 MiB to the existing local event-log
append route with explicit source_event_id identity mapping and a
deterministic scope-derived source and case namespace. OWG's existing append
transaction deduplicates exact source-key replays and refuses conflicting
source revisions. No new source watermark is created. The journal is not
deleted or altered by a successful append. The target log and source identity
mapping must remain consistent.

The result reports only counts (added, duplicate, rejected, journal total),
batch number and explicit unknown-history/source-write flags. It never echoes
the dashboard session, ServiceNow token, raw provider IDs, descriptions,
journal path or provider payload. If the server returns rejected rows, the
CLI reports completed_with_rejections and exits 3: inspect the saved log
before any further action. A transport failure, unexpected HTTP response,
malformed receipt or revocation detected **after attempting POST** is reported
as unverified with possible_local_append=true; the transaction may have
committed. **Do not automatically retry**: inspect OWG's append history
first. Before any HTTP attempt, failures are refused with no local write.

## Trust boundaries and limitations

The journal and log still contain business metadata, even though incident
IDs are hashed and raw descriptions/callers are omitted. Apply organizational
retention, backup encryption, access control and deletion policy. This
command does not add a centrally managed credential vault, atomic revocation
fence, retention scheduler or independently verified ServiceNow tenant ACL.
The local grant is not a substitute for provider-side token revocation, and
it cannot prevent a race with an already-started local HTTP append. Never
interpret a current-state snapshot as a complete incident history, verified
business outcome or confirmed employee productivity.

The synthetic acceptance suite exercises actual loopback HTTP transport,
the real OWG event-log engine's source-key append and retry behavior, the
FastAPI route's dashboard session and storage confirmation, and refusal for
wrong target, absent consent, mismatched tenant, revoked grant, invalid
journal, failed local transport and environment proxy injection. These
tests are **not** real ServiceNow, Entra, Okta or customer acceptance. Exact
head native Ubuntu/macOS/Windows pytest, browser, accessibility, scale,
starter, packaging and release checks must pass before merging the draft.

Remaining rollout gaps include authorized disposable ServiceNow sandbox
validation, complete source change history, centrally enforced revocation,
managed credentials, retention policy enforcement and a reviewed enterprise
scheduler. A user still explicitly invokes the journal/poll workflow before
this local append; there is no unattended provider sync.
