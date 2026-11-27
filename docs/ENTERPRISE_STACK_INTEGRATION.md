# Enterprise connector stack integration candidate

This branch consolidates the exact pending enterprise series #1573–#1587
and integrates main as of commit 4679371c00e8b3b19ce2d00999309b1cec794d6b.
The original fifteen draft PRs remain unchanged. Review this candidate as
one delivery unit; do not merge both the consolidated candidate and every
original PR independently. The integration commit retains both histories.
If main advances, compare again before landing.

## Implemented scope

- Common bounded retained ingestion, revision-bound recurring jobs and
  atomic source-key/checkpoint persistence.
- Nine retained adapters: customer REST, Google Workspace, native SQL,
  Slack, Salesforce, ServiceNow, SAP procurement, Dataverse and HubSpot.
- Gateway-scoped workers, durable completion receipts, publication grants,
  reviewed cross-source cases, private crosswalks, saved review versions,
  deployment-managed secret files, selected identity-verified credential
  replacement and dedicated deployment/readiness.
- Existing connector-specific scope and time-precision limitations remain.
  These are selected/bounded evidence adapters, not full source-system
  replication, independently verified business identity or complete CDC.

## Additional integration hardening

The shared provider contract is used by local dispatch, Gateway job schema,
snapshot schema, native crosswalk capability and reviewed rotation support.
A passive capability matrix distinguishes available implementation from
live-provider acceptance. Unknown provider names fail closed. Dispatch
imports only a fixed reviewed adapter module map, not arbitrary supplied
module paths.

REST endpoint allowlist errors are structured again. For deployments that
need exact credential-to-destination association, configure
OWG_REST_ENDPOINT_CREDENTIALS as a JSON object mapping each permitted
endpoint to its exact logical credential reference, e.g.:

    {"https://provider.example.invalid/events":"OWG_REST_TOKEN_PRODUCTION"}

The existing OWG_REST_ALLOWED_ENDPOINTS list still applies. Any malformed
map or unmapped/mismatched endpoint-reference pair prevents reading;
changing the mapping invalidates the current profile binding. Profiles
created while the pair catalog is configured require it subsequently, even
if the environment setting disappears. Existing deployments without the
new catalog retain their previous behavior. For those older profiles,
install the pair catalog and create a reviewed replacement profile when
permanent enforcement is needed. The catalog belongs to the deployment
administrator, not the dashboard user, and contains logical references,
never credential values.

Worker replies now use a bounded 64 KiB streaming read and strict JSON
validation. Expiry is rechecked at the end of basic publication exports
as well as reviewed exports. The POSIX worker fixture is skipped on
non-POSIX platforms, consistent with the dedicated worker contract.

## Acceptance

The focused enterprise-stack-acceptance workflow runs for the consolidated
integration PR even in draft status. It compiles Python modules, checks
enterprise JavaScript and inline Gateway scripts, runs 17 targeted test
modules including disposable PostgreSQL Gateway storage, and exercises
the two enterprise Chromium scripts. Ordinary feature drafts continue to
use the existing smoke workflow. The full repository test workflow and
native SQL TLS service acceptance must still pass before merging.

The new integration fixture explicitly follows three real retained
provider adapters (REST, HubSpot, Dataverse) using synthetic source HTTP
transports, then real storage: publish exact snapshots, prepare typed
private crosswalks, create a reviewed three-source case, persist/reopen
a saved plan, import/replay a combined mining log, revoke a publication
and reject the stale review. Separate scale fixtures exercise four
5,000-event publications and refuse a fifth, rather than silently
truncating the combined 20,000-event budget. Other tests cover provider
registration and REST credential scope drift.

The integration workflow must actually complete successfully; authoring
tests is not evidence that they passed. GitHub Actions results are the
source of truth for this candidate's execution status. Local checkout
was blocked by DNS in the authoring environment. Full real-provider,
managed hardware, Linux service, deployment and performance acceptance
remain separate requirements.

## Landing sequence

1. Run focused acceptance and repair failures on the consolidated branch.
2. Review the full candidate against current main and run required full
   platform, PostgreSQL, TLS and browser checks before merge.
3. Land one reviewed integration unit; then mark the original superseded
   drafts accordingly after verifying the merged main tree. Do not close
   them merely because a candidate exists.
4. Test actual approved provider tenants and source-host deployment;
   record live acceptance evidence independently of synthetic CI.
