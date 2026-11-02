# ServiceNow audit observations: prepare → review → apply

**Status:** optional operator-invoked two-phase workflow, disabled by default. This extends the existing [bounded audit reader](SERVICENOW_INCIDENT_AUDIT.md), not a scheduled feed or certified customer connector.

## Why this exists

The one-command audit importer reads a selected ServiceNow `sys_audit` window and immediately appends its minimized observations to an existing OWG event log. The new `server.servicenow_incident_audit_review` command separates those actions so an operator can inspect the **exact prepared CSV and its SHA-256 digest** before explicitly allowing a local append. `prepare` does not POST to OWG; `apply` never contacts ServiceNow. Both refuse absent or revoked grants and mismatched tenant/source scope.

The CSV contains only hashed source case/event identities, selected field-change activity labels and UTC timestamps. No old/new values, ticket descriptions, names or comments are requested. Observations do not establish full source history, successful business outcomes or verified employee activity.

## Explicit authorization

Use a private owner-only directory (0700 on POSIX), an existing enabled selected-incident grant and a **separate** enabled audit grant matching the exact host, tenant, selected incident IDs, fields, local time window and timezone. See [audit grant example](SERVICENOW_INCIDENT_AUDIT.md). The target log must already exist and have exactly the matching source namespace. Use an authorized, least-privilege read-only ServiceNow bearer credential and an authenticated OWG local dashboard session.

Set environment variables `OWG_SERVICENOW_TOKEN` and `OWG_DASHBOARD_SESSION` in the operator's secure environment. Never place tokens in flags, CSV or grants.

### 1. Prepare without append

```sh
python -m server.servicenow_incident_audit_review prepare \
  --instance example.service-now.com --tenant demo \
  --incident-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --field state --field priority \
  --since "2026-10-30 00:00:00" --until "2026-10-31 00:00:00" \
  --timezone UTC \
  --grant-file /private/snow/incident-grant.json \
  --audit-grant-file /private/snow/audit-grant.json \
  --csv /private/snow/audit-review-001.csv \
  --manifest /private/snow/audit-review-001.json \
  --log-id log_0123456789abcdef \
  --confirm-log-id log_0123456789abcdef --port 8765 \
  --consent-audit-read --consent-recovery-export
```

This verifies the local destination *before* the provider request, makes bounded GET requests under both grants, and creates two **new**, non-overwritten private artifacts. The receipt includes `csv_sha256`, `audit_observations`, `review_required=true` and `local_owg_append_performed=false`.

Inspect the CSV, count, selected window, manifest and SHA-256 independently. A manifest binds the exact selection digest, source namespace, target log ID, fields, time window, timezone, row count and CSV hash. It is a local integrity/review aid, **not** a cryptographic signature against another process with the same OS account.

### 2. Explicitly apply the reviewed bytes

```sh
python -m server.servicenow_incident_audit_review apply \
  --instance example.service-now.com --tenant demo \
  --incident-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --field state --field priority \
  --since "2026-10-30 00:00:00" --until "2026-10-31 00:00:00" \
  --timezone UTC \
  --grant-file /private/snow/incident-grant.json \
  --audit-grant-file /private/snow/audit-grant.json \
  --csv /private/snow/audit-review-001.csv \
  --manifest /private/snow/audit-review-001.json \
  --log-id log_0123456789abcdef \
  --confirm-log-id log_0123456789abcdef --port 8765 \
  --confirm-sha256 REPLACE_WITH_THE_EXACT_64_CHARACTER_PREPARE_DIGEST \
  --consent-prepared-read --consent-local-append --consent-reviewed-apply
```

`apply` verifies owner-only files, exact manifest keys/types, SHA-256 and explicit confirmation, bounded CSV schema/identity/time/field constraints, current **both** grants, authenticated loopback target and matching log namespace. Only then does it issue one authenticated OWG append POST. Replays of the same source event IDs use OWG's transactional deduplication. A zero-row CSV makes no POST.

**Do not automatically retry** an `append_unverified` receipt (exit code 3): the local write might have committed despite a lost response. Inspect the local event log and retained private artifacts first. A preflight refusal (exit code 2) does not initiate a local POST. The two files are intentionally retained for operator-controlled retention/deletion; this command does not implement background deletion or source-token revocation.

## Boundaries and validation

The provider read inherits the existing pinned `*.service-now.com` HTTPS host, TLS verification, no proxy/redirect, at most 8 incidents, 7 selected fields, a 7-day window, 4 × 100 rows, 256 KiB per response and separate owner-only grants. The prepared CSV uses the existing 2 MiB OWG append limit. No external business writes, scheduled collection, central revocation, managed credentials, full audit-history guarantees or hidden cloud upload.

`tests/test_servicenow_incident_audit_review.py` exercises the fake provider with the **actual disposable local OWG HTTP/storage append engine**, including prepare-without-append, explicit apply, deduplication, digest/manifest tampering, cross-tenant refusal, revoked grants, missing consent, zero-row reads and uncertain POST outcomes. These are synthetic integration tests, not real ServiceNow tenant acceptance. All native Ubuntu/macOS/Windows pytest, browser, accessibility, scale, starter and packaging/release exact-head gates must pass before merge. Real customer-controlled ServiceNow sandbox acceptance remains outstanding.
