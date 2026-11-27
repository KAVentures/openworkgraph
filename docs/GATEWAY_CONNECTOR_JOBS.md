# Enterprise Gateway connector jobs

This increment of #1572 adds a central, customer-hosted connector control
plane over the local enterprise feed runtime from #1573–#1576. It is not
a shared raw-event warehouse or a multi-tenant provider secret vault.

## Architecture and authority

- The existing named-administrator session middleware (owner/admin write,
  viewer read) authorizes job registration, pause, recovery and worker
  credential rotation. Organization IDs scope every job and history query.
  Gateway administrators retain their existing instance-wide authority.
- A job pins one immutable local profile ID, revision and canonical SHA-256
  of its provider and public configuration. No provider token or full
  profile configuration is transmitted to the Gateway. The worker credential
  is distinct from employee/device/integration credentials, unique to one
  job and stored only as a hash by the Gateway.
- Multiple Gateway replicas coordinate leases through PostgreSQL database
  transactions and per-organization row locks. SQLite is the development
  equivalent using BEGIN IMMEDIATE. No in-memory scheduler is authoritative.
- The worker executes one approved source profile on a dedicated, local
  event-log workspace. REST, Google Workspace, SQL and Slack reuse their
  existing extraction, credential binding, event identity, checkpoint and
  retention contracts. Local source credentials and raw retained data never
  enter the Gateway control plane. Multiple independent workers can operate
  across different dedicated source workspaces.
- Gateway reports are worker-reported operational metadata, not independent
  provider validation, actual business outcomes or raw event-log sharing.
  They contain job/profile pins, timestamps, state, bounded counts and
  fixed outcome codes. Organization administrators can inspect up to 100
  recent job history entries; existing Gateway audit policy also applies.

## Register and run

1. On a POSIX customer-controlled source host, configure the local feed
   with the existing enterprise connector dashboard. Review all source
   scopes and local retention approvals. Run a manual acceptance window
   if appropriate. Stop the local scheduler and dashboard/server process
   before handing the workspace to the dedicated worker. Do not have
   multiple workers or desktop processes writing the same DuckDB workspace.
2. On that host run:

       python -m server.enterprise_connector_worker --describe-profile rest_...

   It prints only the profile ID, provider, revision and SHA-256 pin.
3. Sign into Gateway administration and open **Enterprise connector jobs**.
   Register the reviewed profile with its exact pin, recurring interval
   (15–10080 minutes) and explicit dispatch and operational-receipt consent.
   A dedicated worker token is shown once. Copy it into the host's secret
   environment, not into source code, a public issue or command-line arguments.
4. Configure the dedicated process with these environment variables:

       OWG_CONNECTOR_WORKER_APPROVED=true
       OWG_CONNECTOR_GATEWAY_URL=https://your-gateway.example
       OWG_CONNECTOR_ORGANIZATION=your-organization
       OWG_CONNECTOR_JOB_ID=cj_...
       OWG_CONNECTOR_PROFILE_ID=rest_...
       OWG_CONNECTOR_PROFILE_REVISION=1
       OWG_CONNECTOR_PROFILE_SHA256=<reviewed pin>
       OWG_CONNECTOR_OUTBOX=/private/worker-outbox
       OWG_CONNECTOR_WORKER_TOKEN=<one-time job token>

   Also configure the existing OWG data/auth/config paths and exact
   provider credential environment on the source host. Use a private
   outbox directory (0700) on durable storage. The existing local recording
   pause must permit scheduled reads; a paused recorder causes a skipped
   window, not a bypass. Remove competing local schedules for this profile.

5. Start with `python -m server.enterprise_connector_worker --once` to
   inspect one poll; omit --once to poll every 30 seconds. Use a dedicated
   customer-managed service supervisor for restart and logs. HTTPS is
   mandatory except loopback development. Redirects and environment HTTP
   proxies are disabled. Keep host clocks synchronized.

## Recovery

One job has at most one active 240-second Gateway lease. Each local read
retains its own 120-second source lease and transactional checkpoint. The
worker saves a private durable intent before starting a read, then saves a
completion receipt before attempting delivery. A lost completion response
can be replayed with the same receipt and lease; the Gateway accepts exact
idempotent replay, rejects conflicting receipts and enforces monotonic
success checkpoints. The worker does not claim another job while a pending
outbox exists. A crash with only an intent produces an ambiguous blocked
receipt, never an automatic repeated source read.

A lost claim response, expired lease or invalidated active lease may hide a
committed local window. The Gateway pauses rather than automatically retry
in these cases. Administrative pause/rotation cannot recall a provider
request already in flight or reverse a committed local transaction.
There is no distributed exactly-once transaction across the source
database and Gateway. To recover, stop the old worker, inspect the local
profile/checkpoint, retained log and private outbox, reconcile the actual
state, and only then explicitly confirm ambiguous recovery in Gateway.
If the old outbox is rejected because the lease or credential was invalidated,
archive it securely on the host after reconciliation and restart with the
current approved token; do not silently discard it. Rotating a worker
credential requires the job to be paused and keeps it paused.

Rate limits record bounded cooldowns; administrative resume does not waive
a known cooldown. Five consecutive reported failures pause the job.
A changed local profile needs a newly approved job/profile assignment;
this increment does not silently rebind profiles or migrate source secrets.
Job configuration is immutable apart from operational enable/disable and
worker token rotation. Job retirement/deletion, automated local receipt
reconciliation and credential-vault orchestration remain follow-ups.

## Acceptance and explicit boundaries

- `tests/test_gateway_connector_jobs.py`: SQLite and PostgreSQL actual
  database concurrency, job-scoped credentials, tenant isolation, stale
  revisions, expiry, rotation, idempotency, bounded history, admin roles.
- `tests/test_enterprise_connector_worker.py`: real local retained-log
  transaction with mock provider HTTP, Gateway receipt, lost-response
  recovery, ambiguous intent and local pause/competing schedule guards.
- CI exercises PostgreSQL in its existing disposable service job.

This is a central scheduling and status plane for dedicated source hosts.
It does not synchronize their raw logs to the Gateway, automatically deploy
workers, provide an enterprise secret manager, support Windows worker
service installation, or establish production-scale throughput. Independent
live-provider testing and measured multi-host acceptance remain necessary.
