# Enterprise connector managed credential delivery and rotation

Enterprise delivery #1572. Extends the retained connector and Gateway job
stack through #1585. This is source-host secret delivery and reviewed
credential replacement, not a hosted secrets manager.

## Deployment-managed secret files

Existing logical credential environment references continue to work.
On a POSIX ingestion host, deployment management can instead materialize
secret files under a dedicated owner-only directory and configure:

    OWG_CONNECTOR_SECRET_ROOT=/absolute/private/connector-secrets
    OWG_CONNECTOR_SECRET_FILES={"OWG_HUBSPOT_TOKEN_PRODUCTION":"hubspot.token","OWG_DATAVERSE_SECRET_PRODUCTION":"dataverse.secret"}

The example names refer to files, not secret contents. Configure the
existing connector catalogs to use the same logical credential references.
Supported reference families:

- OWG_REST_TOKEN_* (generic REST)
- OWG_SLACK_TOKEN_* (native Slack)
- OWG_SAP_P2P_* (native SAP procurement)
- OWG_DATAVERSE_SECRET_* (Dataverse application secret)
- OWG_HUBSPOT_TOKEN_* (HubSpot private application)
- OWG_SQL_DSN_* (native PostgreSQL source DSN)

Use a private directory owned by the ingestion OS user (0700) and regular
single-link files owned by that user (0600). Every ancestor must be a real
directory, owned by root or the ingestion user, and not writable by
others except sticky directories. Symlinks, hardlinks, special files,
overlarge files and unsafe paths are rejected. The resolver opens directory
components and the final filename with no-follow semantics; reads are
bounded to 16 KiB, and file metadata is rechecked after reading. A single
trailing LF or CRLF is accepted. Internal line breaks/control characters
are rejected. Existing provider-specific secret format, length and DSN
checks continue to apply.

An explicitly mapped file is authoritative: if it is missing, malformed
or inaccessible, the resolver fails closed. It never falls back to a
stale environment secret. Unmapped logical references retain environment
delivery. Duplicate references or duplicate filenames invalidate the
catalog. Secret paths and contents are not accepted from the dashboard or
Gateway. The catalog is deployment-owned. This protects against unintended
local exposure and path redirection, not a malicious process with the
same OS identity or a privileged administrator.

Use your deployment tool, vault agent, or controlled provisioning process
to materialize owner-only **regular files**. Kubernetes projected-secret
volumes often use symlinks and cannot be consumed directly under this
contract; use a trusted materialization step. The adapter does not contact
a vault or cloud secrets API, renew vault leases, or provision credentials.
Windows keeps the existing environment delivery; file delivery requires
POSIX no-follow directory-descriptor support. Existing OAuth providers and
SQLite source files keep their own credential/storage contracts.

To update a file, prepare a new owner-only regular file in the same private
directory, flush it and atomically replace the old filename. A changed
secret still invalidates the profile binding. The file resolver does not
automatically authorize rotation.

## Reviewed in-place rotation

Only HubSpot and Dataverse currently support this operation because their
adapters can verify the exact configured account/application or organization
and application-user identity without reading event records. Other providers
must continue using a newly reviewed profile when their credential changes.
The operation does not prove that the replacement has the same complete
record visibility or permissions; Dataverse WhoAmI in particular is not a
role/ACL audit.

1. Pause the Gateway job and local recurring schedule. Stop the old worker
   and reconcile any lease, uncertain result and durable outbox. Ensure
   the local profile is disabled. A disabled revision fences an old local
   retention transaction but cannot undo a provider request already sent.
2. Install the replacement secret via deployment management, keeping the
   exact reviewed source catalog unchanged. Do not paste it into OWG.
3. In Enterprise connector operations choose **Verify and bind replacement
   credential** on the disabled supported profile. Confirm worker/outbox
   reconciliation and identity-only provider authentication.
4. HubSpot validates the exact account, app and approved scopes. Dataverse
   obtains a tenant-specific application token and validates organization
   and application-user IDs via WhoAmI. No event-data requests are made.
5. The local service rechecks the disabled revision, source definition,
   candidate secret, and retained dataset before committing the new
   binding. Concurrent profile/secret changes fail closed. The revision
   advances, but the existing pseudonym key, profile ID, log ID and
   checkpoint remain unchanged. The feed stays disabled. Rate-limit
   cooldown is not waived by rotation.
6. Verify the resulting profile, enable explicitly, and remove/recreate
   old revision-pinned local schedules if appropriate. Reauthorize
   recurring execution for the exact enabled revision.

The passive local profile response exposes delivery kind, support for
reviewed rotation and the last identity-verification timestamp. It
does not expose the secret value, private binding digest or file path.
The local endpoint POST /v1/enterprise-connectors/rest/{profile_id}/rotate-credential
requires a dashboard session, exact disabled revision and three explicit
confirmations. It does not accept a replacement credential in the body.

## Gateway profile rebind

A local revision change invalidates the old Gateway job pin. The Gateway
owner may now explicitly rebind the same profile ID to a strictly newer
revision and changed configuration digest, without creating a duplicate
job. The source host is authoritative; this is owner attestation, not
independent Gateway verification of the configuration or identity.

After local reauthorization, use the worker --describe-profile command
to obtain the new exact profile ID, revision, digest and provider.
Remove or expire any old central publication for that job (saved reviews
pinned to the removed grant become unusable). In the paused Gateway job,
open **Rebind a newer revision of this same local profile**. Review the
new pin and confirm same-source identity, old worker reconciliation and
dispatch/receipt consent. The owner-only API checks the current job
revision, old profile pin, strictly newer revision, paused/lease-free
state and absence of an unexpired prior publication in the same
organization transaction.

Rebinding atomically revokes the old worker credential, issues a new
one-time token, resets completion replay state and keeps the job paused.
The job ID, original source profile ID, provider, historical receipts,
last success and existing due/cooldown time are preserved. Configure the
new worker and explicitly resume the exact job. Never reuse an old
worker outbox or claim token after rebinding. The operation cannot recall
previously downloaded event snapshots or cancel already sent source reads.

The Gateway endpoint POST /v1/admin/connector-jobs/{organization_id}/{job_id}/rebind
is owner-only. A normal admin can still perform existing job actions
allowed by their role. No provider credential or secret-file path is sent
to Gateway.

## Validation and outstanding work

Regression fixtures cover file delivery, atomic replacement, permission,
link, special-file, size and encoding rejection, invalid catalogs,
environment compatibility, source credential identity mismatch and
concurrent replacement, unchanged log/key/checkpoint, exact revision
fencing, old worker token revocation and active publication blocking.
SQLite/PostgreSQL CI registration includes the new test file.

Full repository CI and live HubSpot/Dataverse sandbox validation are still
required. File-backed secret delivery is not automatic rotation, direct
Vault/AWS/Azure secret-manager integration, managed identity, certificate
authentication or OS keychain integration. The remaining adapters require
a provider-specific identity assurance design before in-place rotation.
