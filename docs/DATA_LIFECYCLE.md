# OpenWorkGraph Gateway data lifecycle

OpenWorkGraph is local-first. This document covers lifecycle controls for evidence that an endpoint has **already synchronized to a self-hosted organization Gateway**. It does not change or remotely delete the employee/device's local OpenWorkGraph database.

## Safety model

Lifecycle controls are deliberately opt-in:

- organization retention is **disabled by default**;
- changing retention configuration does not itself physically delete rows;
- the canonical workflow-trace layer applies an enabled retention cutoff as a server-side visibility floor;
- physical deletion is a separate dry-run-first operation;
- arbitrary purge requires at least one explicit selector, unless the administrator deliberately chooses the whole organization;
- destructive CLI operations require an organization-specific confirmation string;
- evidence deletion does not delete the Gateway audit log.

This means an existing v0.53.1 deployment that never configures lifecycle policy keeps the same evidence visibility and storage behavior.

## Configure retention

The lifecycle CLI uses the same `OWG_GATEWAY_DATABASE_URL` / Gateway environment configuration as the server.

Show the current policy:

```bash
python -m gateway.lifecycle status --organization acme
```

Set a 30-day retention policy:

```bash
python -m gateway.lifecycle set-retention --organization acme --days 30
```

Disable retention again without deleting remaining evidence:

```bash
python -m gateway.lifecycle set-retention --organization acme --disable
```

When retention is enabled, `/v1/workflow-trace` and the search/transfer interfaces that build on the canonical trace cannot widen a query earlier than the organization cutoff. The cutoff is re-applied on every cursor page, so an older cursor cannot bypass a policy that was tightened after pagination began.

## Logical retention versus physical deletion

Retention has two stages on purpose.

1. **Logical visibility:** the canonical Gateway evidence trace stops returning evidence older than the retention cutoff.
2. **Physical cleanup:** an administrator or scheduled job removes expired rows from `evidence_events`.

Preview a cleanup:

```bash
python -m gateway.lifecycle apply-retention --organization acme
```

Execute it:

```bash
python -m gateway.lifecycle apply-retention \
  --organization acme \
  --execute \
  --confirm 'APPLY acme'
```

For production, run the apply command from a customer-controlled scheduler (for example cron, systemd timer, Kubernetes CronJob, or the organization's normal job runner). Keeping the physical sweep explicit avoids introducing a hidden destructive background worker into existing deployments.

## Retroactive evidence purge

Purge supports time, actor, device, session, and event-type selectors. It is a dry run unless `--execute` is supplied.

Preview deleting synchronized evidence for one actor:

```bash
python -m gateway.lifecycle purge \
  --organization acme \
  --actor employee-123
```

Execute the same purge:

```bash
python -m gateway.lifecycle purge \
  --organization acme \
  --actor employee-123 \
  --execute \
  --confirm 'DELETE acme'
```

Other examples:

```bash
# Evidence observed before an explicit instant
python -m gateway.lifecycle purge --organization acme --before 2026-09-01T00:00:00Z

# One device only
python -m gateway.lifecycle purge --organization acme --device laptop-42

# One capture session only
python -m gateway.lifecycle purge --organization acme --session session-id

# Whole organization: must be explicitly requested
python -m gateway.lifecycle purge --organization acme --all-evidence
```

`--all-evidence` cannot be combined with selectors. An unqualified purge without either a selector or `--all-evidence` is rejected.

## Tenant isolation

Every lifecycle count/delete operation includes `organization_id` in the database predicate. Actor/device/session selectors are therefore always subordinate to the organization boundary. Deleting `actor_id=alice` in organization `acme` cannot delete an actor with the same identifier in another organization.

## Audit behavior

Lifecycle configuration changes, retention previews/applies, and purge previews/operations are written to the existing Gateway audit log by the CLI. Purge audit entries record selector **types and counts**, not the deleted window titles, metadata, copied content (which OpenWorkGraph never captures), or selector identity values.

The audit log is intentionally not removed by evidence purge. Organizations that need a separate audit-log retention policy should manage that as a distinct control rather than silently coupling it to evidence deletion.

## Backups and storage reclamation

Deleting live Gateway rows does not by itself erase customer-controlled database backups, snapshots, replicas, exports, or downstream copies. Those systems need their own retention/deletion procedures.

Likewise, deleting rows does not guarantee immediate filesystem block reclamation. PostgreSQL vacuum/autovacuum and SQLite maintenance determine physical space reuse. OpenWorkGraph reports deletion of logical database rows; it does not claim secure media erasure.

## Local endpoint data is separate

Gateway deletion never sends a deletion command back to endpoints. An endpoint may still retain its local evidence according to the endpoint/user's own policy. This is intentional: the self-hosted Gateway is an optional synchronized organizational data plane, not the owner of the employee's local source-of-truth database.

## Compliance note

These controls are technical building blocks for retention and erasure workflows. They are not, by themselves, a claim of GDPR or other regulatory compliance. Organizations remain responsible for defining lawful retention periods, backup handling, access controls, employee notice/consultation where applicable, and any required records of processing or deletion.
