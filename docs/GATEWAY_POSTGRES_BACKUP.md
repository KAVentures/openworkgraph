# PostgreSQL Gateway backup and recovery rehearsal

Parent: #1572, #326, #370 R20. This operator-only CLI complements the customer-owned self-hosted Gateway. It does not expose a backup API, make remote provider requests, enable sharing, change retention or restore over production. It is not a replacement for encrypted infrastructure backups or point-in-time recovery.

## Requirements

- Install OWG with the existing Gateway dependencies (python -m pip install -e ".[gateway]").
- Install compatible native pg_dump and pg_restore clients. Prefer matching source and rehearsal PostgreSQL major versions.
- Run on a trusted operator-controlled host. Source credentials need access to dump and count the complete database, including every relevant schema. Row-security restrictions fail closed rather than silently exporting a partial population.
- Use an independently managed, network-isolated rehearsal PostgreSQL cluster with no customer application or external integrations and a maintenance account allowed to create/drop temporary databases. Merely choosing another database on a production server is not sufficient.
- Store bundles on encrypted customer-controlled storage with appropriate backup access, retention and destruction policies. The CLI does not implement encryption or signing. On Windows, ensure equivalent restrictive filesystem ACLs externally.

## 1. Create a backup

Set OWG_GATEWAY_DATABASE_URL through the operator's secret-management mechanism to an explicit PostgreSQL URI, for example a URI with an explicit host, database, user and sslmode=verify-full. Do not copy real connection strings into command arguments, shell history, documentation, CI or support tickets. The CLI refuses ambient PG-prefixed environment variables and ambiguous libpq options. It supports explicit TLS settings and a single TCP host. The example deployment's default URL omits sslmode, so operators must explicitly select an appropriate policy; sslmode=disable is for isolated non-TLS development networks only.

    python -m gateway.postgres_backup create --bundle /secure/backups/owg-2026-11-27 --confirm "BACKUP ALL ORGANIZATIONS"

The destination must be new and its parent must exist. On POSIX the new directory and files are owner-only. The backup covers the **entire PostgreSQL database, all organizations**, not a tenant-selective export. It can contain sensitive evidence, token hashes, identity configuration and materialized work text. A PostgreSQL read-only repeatable-read transaction exports one MVCC snapshot; the inventory and pg_dump use that same snapshot. Exact table counts and a limited schema fingerprint (live columns and constraints) are summarized in the manifest, rather than individual table counts or names. A full logical dump may be expensive and holds a snapshot open; schedule it with the DBA and measure its production impact.

A bundle contains gateway.pgdump and manifest.json. The manifest is written last. Interrupted or failed creation leaves an incomplete private directory without a valid manifest; do not treat it as a valid backup. Remove incomplete bundles according to customer policy. The timeout bounds the native client invocation, not every SQL query or the full operation. Apply infrastructure-level limits separately.

## 2. Verify offline

    python -m gateway.postgres_backup verify --bundle /secure/backups/owg-2026-11-27

This checks the fixed manifest schema, private file boundaries, byte length and SHA-256 digest. It does not contact a database. Hash agreement detects accidental change, not malicious replacement of both archive and manifest. Verification does not prove a PostgreSQL archive is restorable. Use authenticated storage or separately controlled signatures for provenance.

## 3. Rehearse on an isolated cluster

Set OWG_GATEWAY_REHEARSAL_DATABASE_URL to the independent cluster's postgres maintenance database, and keep OWG_GATEWAY_DATABASE_URL available for the source-endpoint guard.

    python -m gateway.postgres_backup rehearse --bundle /secure/backups/owg-2026-11-27 --confirm "REHEARSE ON ISOLATED CLUSTER"

A matching configured source endpoint is refused. Host aliases and routing can hide a shared cluster; this guard is not a network-isolation proof. Ensure cluster isolation independently. A verified private copy of the archive is made before restore, so allow additional disk space. The tool creates a fresh randomly named database from template0, restores in one transaction without original owners or grants, compares exact table-count and limited schema digests, and drops only the database it created. It never uses pg_restore --create, --clean or a production database name. If cleanup fails, the CLI reports failure; a qualified DBA must inspect the dedicated rehearsal cluster and remove the abandoned generated database after checking its identity. Never automate wildcard deletion against a production cluster.

Only rehearse archives from a trusted source: logical PostgreSQL restoration executes database definitions and is not a sandbox for malicious SQL. A passing rehearsal establishes logical restore and selected inventory agreement for that archive, not exact row-content equality, sequence-state equivalence, all index/function definitions, database roles, permissions or live Gateway behavior.

## Operational recovery decision

Before any real recovery, keep the restored environment isolated and obtain separate administrator authorization. Reconcile all post-backup changes: offboardings, revoked credentials, retention and purge decisions, consent changes, provider configuration, pending jobs and integration permissions. Old backups may contain data that has since been deleted and credentials that have since been revoked. Do not reconnect employees, agents or provider workers until those obligations are independently verified. The CLI intentionally has no promotion command.

Database roles, externally managed credentials, TLS, OIDC/SSO configuration, pseudonymization/signing keys, infrastructure files and other external dependencies need separate secure backup and recovery plans. A logical dump is not a WAL/PITR plan. Customer operators must measure their recovery-point and recovery-time objectives, test cross-version compatibility, and enforce backup retention even after live-row purges.

## Validation

The dedicated GitHub Actions workflow starts two synthetic PostgreSQL services on different ports and exercises snapshot creation, archive verification, isolated restoration, exact table inventory agreement and cleanup, including a fixture with revoked credential data. The ordinary regression suite exercises refusal of malformed manifests, bad checksums, unsafe permissions, conflicting connection parameters, overwrite attempts and missing confirmations. No customer data or backup archive is uploaded by CI.

A real customer restore drill and measured backup/restore times remain separate enterprise acceptance gates.
