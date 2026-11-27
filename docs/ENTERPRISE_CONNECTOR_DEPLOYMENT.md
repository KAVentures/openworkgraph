# Dedicated enterprise connector deployment and readiness

Enterprise delivery #1572. Builds on the retained connector, Gateway job,
publication, private crosswalk and managed credential stack through #1586.
The dedicated worker remains customer-hosted and POSIX-only. The Gateway
coordinates a job, not arbitrary remote shell execution.

## Prepare a dedicated ingestion host

Use a pinned, reviewed OpenWorkGraph release or commit and its existing
Python environment/dependencies, including DuckDB and any required native
SQL driver. Run each dedicated workspace as its own nonprivileged OS user.
Do not share an active DuckDB workspace with a desktop server or another
connector worker process. The existing per-outbox lock serializes worker
polls sharing that outbox; it is not an OS-wide workspace ownership lock.

The example systemd template in deploy/enterprise/owg-connector-worker@.service
and connector-worker.env.example are deployment examples, not an installer.
Review them with your infrastructure administrator. Install the application
in a root-managed, read-only /opt/openworkgraph tree, create the
owg-connector-INSTANCE user/group, and provision a private per-instance
state directory under /var/lib/openworkgraph-connectors/INSTANCE. Use an
instance name suitable for both the OS account and systemd template.
Prepare the local retained workspace and explicitly approved profile using
the existing local configuration workflow; stop the setup server before
running the dedicated worker. Preserve workspace backups and encryption
keys according to the existing backup policy.

Prepare private secret files as described in
ENTERPRISE_CONNECTOR_MANAGED_CREDENTIALS.md. The logical
OWG_CONNECTOR_WORKER_TOKEN reference now also supports that delivery.
Existing environment-token delivery remains available. A mapped file
failure never falls back to an old environment token. The same resolver
is used by the event snapshot publication CLI. The worker reads its token
on process startup, not automatically on file replacement: stop/reconcile,
rotate the worker credential and restart using the reviewed new token.
Do not put provider or worker credentials in command-line arguments.

Copy and customize the environment example using a root-only file at
/etc/openworkgraph/connector-worker-INSTANCE.env. Replace every placeholder
and configure the actual reviewed provider-specific catalogs and secret
references. Confirm the local workspace, auth/config paths and capture
state are the intended ones. The supplied example is a generic REST
configuration; substitute the relevant native provider catalog. The
service has no capabilities, uses a private tmp/device namespace, protects
the system and home directories, and permits writes only to the per-instance
state directory. Test compatibility with local drivers, CA files, OAuth
storage and organizational policy. Network firewall/egress policy is
deployment-owned; systemd address-family restrictions are not a host allowlist.

## Exact deployment preflight

Obtain the local profile pin with the existing command:

    python -m server.enterprise_connector_worker --describe-profile PROFILE_ID

Review it against the Gateway job and provision the exact job-specific
worker token. With the same reviewed environment, invoke:

    python -m server.enterprise_connector_worker --preflight

For a separately authorized non-dispatching Gateway check:

    python -m server.enterprise_connector_worker --preflight --inspect-gateway

Run these as the dedicated service account with the same environment and
canonical paths as the eventual service. Preflight does not require the
execution approval flag, claim a lease, read the provider, refresh OAuth,
modify retained events, create an outbox or enable recording/schedules.
It checks POSIX support, existing workspace, local profile pin, local
authorization, available credential binding, cooldown, interrupted run,
competing enabled local schedule, recording state and private pending
receipt/intent. If the workspace does not exist it is not created.
Inspecting an existing workspace can initialize/upgrade its existing
schema and permission metadata through the common workspace accessor; it
is not a byte-for-byte read-only database check.

Remote inspection uses only the job token and exact profile pin.
It serializes with token rotation and profile rebind, reports the current
job state and lease, and does not change dispatch authorization, lease,
job revision or history. It does not independently verify the provider or
the local source data. Remote inspection is not sent by default.
A blocked check yields exit code 2; warnings and unchecked remote state
require operator review. The JSON is sanitized: no raw event rows, secret
values, lease IDs or private file paths. A ready-for-review result is
not an execution grant or a provider connectivity test.

## Explicit recording-state gate

The legacy worker continues to use the ordinary local recording-state
behavior unless configured otherwise. For dedicated enterprise hosts set
OWG_CONNECTOR_REQUIRE_EXPLICIT_RECORDING=true, as the service template
does. This mode requires a valid, owned, regular capture-control state
file in the existing workspace location. Missing, unreadable, malformed,
duplicate-key, symlinked or unrecognized state fails closed. Only
version 1 with state recording permits a source read. Set that state
through the existing local capture-control workflow under explicit
operator approval; the worker does not create or change the file.
The check is repeated before a claimed read. It does not cancel a provider
request already in flight if recording is paused afterwards.

## Latest-only worker readiness

Opt in with OWG_CONNECTOR_REPORT_READINESS=true (enabled in the service
template). This sends a job-token-scoped fixed status code and exact pin
to POST /v1/connector-worker/readiness. The Gateway stores at most one
latest observation per job, coalescing reports within 30 seconds. The
Gateway console shows the code, observed time and a stale indication
after 120 seconds. Values represent worker-reported observations, not
independent health, permission or connectivity verification.

A disabled/missing local profile, changed pin, conflicting schedule,
recording pause, local cooldown or interrupted run can now be observed
without consuming a Gateway lease. A report never enables, pauses or
reschedules the job, changes its dispatch revision, clears a lease or
asserts committed events. A separate exact claim is still required.
Older Gateway instances do not implement these endpoints; leave telemetry
off until the matching Gateway is upgraded. If opted-in reporting fails,
the worker refuses a new claim for that poll rather than bypassing it.
Pending receipt recovery takes precedence over optional heartbeat traffic.
A stale readiness observation is not proof that a process is down, e.g.
when recovery or a provider read takes time. Token rotation and profile
rebind clear the old observation.

## Service operation and recovery

After reviewing preflight, exact Gateway job pin, credentials, capture
state and separate local/Gateway execution consent, set
OWG_CONNECTOR_WORKER_APPROVED=true. Install/reload the reviewed unit and
start the chosen service instance using your normal deployment tooling.
The template does not enable or start anything automatically. Check the
journal and Gateway job view; the service polls every 30 seconds and
uses the existing job lease/backoff and source budgets.

The pending receipt file is a private durable journal, not a queue of
commands. The worker validates the absolute canonical owner-only outbox,
private single-link regular lock file and the bounded single-link pending
file with no-follow/nonblocking opens. Strict JSON, exact state fields,
organization/job/profile digest and receipt/intent model validation are
required. Invalid or foreign files are not replayed or overwritten.
A pending intent is conservatively converted to an uncertain completion;
a pending receipt is replayed to the same exact Gateway job without reading
the provider again. After acknowledged completion, unlink and fsync the
outbox directory. Pending receipts after a rejected/expired lease or
credential rebind require manual reconciliation; the worker never
silently discards them. Pause the job, stop the worker and compare the
local checkpoint and retained log with Gateway history before archiving
any pending artifact. Follow the #1586 credential replacement and job
rebind procedure for changed source pins. Never reuse an old outbox with
a different job or profile.

## Acceptance and limits

Backend fixtures exercise actual retained workspace/Gateway transport,
non-dispatch preflight, local blockers, optional heartbeat, coalescing,
staleness, token rotation, malformed private files and explicit recording.
Full CI, Linux service installation/upgrade testing, live provider
acceptance, multi-host failover and performance tests remain required.
The deployment example is not a fleet orchestrator, managed Kubernetes
operator or automatic installation/upgrade system. It does not create
cloud vault credentials or prove tenant permissions, provider completeness
or independent source health.
