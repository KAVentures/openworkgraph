# ServiceNow selected incident audit observations → local OWG

**Status:** optional, disabled by default. This is a bounded source reader, not a complete ServiceNow history feed, a managed scheduler, or a certified customer integration.

## Functional path

Run the module server.servicenow_incident_audit to make **real HTTPS GETs** to the selected ServiceNow instance's sys_audit Table API. It reads only selected incident IDs, selected *field names* and an operator-approved time window. It converts audit-row observations into UTC source-keyed events, writes a **new private recovery CSV**, and explicitly appends through OWG's authenticated **loopback** event-log REST API. Repeated imports of the same source audit row have the same source event ID and use OWG's transactional deduplication.

Only sys_id, documentkey, fieldname, sys_created_on and tablename are requested. **No oldvalue, newvalue, description, comments or other source content is requested or imported.** Activity labels say "Incident audit: state" or the selected field, not what changed or why. These are field-change observations, not verified state transitions or business outcomes.

This complements current-state selected-incident polling. It does **not** modify the snapshot journal or checkpoint, and never advances their watermark.

## Independent, explicit authorization

The existing owner-only selected-incident grant remains required. Additionally, create a separate owner-only audit grant in a private directory (0600 file / 0700 directory on POSIX), with the exact incident IDs, fields and window:

    {
      "version": 1,
      "enabled": true,
      "consented": true,
      "host": "example.service-now.com",
      "tenant_scope": "demo",
      "sys_ids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
      "table": "sys_audit",
      "fields": ["state", "priority"],
      "since": "2026-10-30 00:00:00",
      "until": "2026-10-31 00:00:00"
    }

The separate audit grant does not imply ServiceNow ACLs or OWG write permission. A real authorized operator must supply a least-privilege read-only ServiceNow bearer credential through environment variable OWG_SERVICENOW_TOKEN and an authenticated local dashboard session through OWG_DASHBOARD_SESSION. Never put secrets in the CLI or grant JSON. The target OWG log must already exist with **exactly one matching source/case namespace**, for example seeded by the existing current-state selected-incident import.

    python -m server.servicenow_incident_audit \
      --instance example.service-now.com --tenant demo \
      --incident-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
      --field state --field priority \
      --since "2026-10-30 00:00:00" --until "2026-10-31 00:00:00" \
      --timezone UTC \
      --grant-file /private/snow/incident-grant.json \
      --audit-grant-file /private/snow/audit-grant.json \
      --output /private/snow/audit-recovery-001.csv \
      --log-id log_0123456789abcdef \
      --confirm-log-id log_0123456789abcdef --port 8765 \
      --consent-audit-read --consent-recovery-export --consent-local-append

All IDs, hosts, paths, sessions and log IDs above are **examples only**. No provider request occurs unless an operator deliberately executes the command with valid current credentials and consent.

## Security and bounds

- **Selection:** 1–8 exact lowercase 32-hex incident IDs; allowed audit fields: state, priority, assignment_group, assigned_to, category, impact, urgency. The audit grant must match the CLI selection exactly.
- **Time:** positive window up to **seven days**, declared IANA source timezone, valid provider-local timestamps. Ambiguous DST folds and nonexistent local times refuse. Provider timezone is operator-declared, not provider-verified.
- **HTTPS:** pinned lowercase *.service-now.com host, GET only, 15-second timeout, system TLS certificate/hostname verification, no proxy, no redirects or provider-supplied next links. Up to **four pages of 100** rows, 256 KiB per response. A full fourth page refuses rather than claiming complete pagination. Wrong table/record/field/window rows refuse.
- **Local data:** SHA-256-derived case and event IDs, no raw provider IDs in CSV or receipts, 2 MiB maximum CSV, new owner-only output path, no overwrite. The authenticated OWG append goes only to literal 127.0.0.1 with exact log confirmation and namespace verification. A zero-row read writes only a header CSV and does not POST.
- **Revocation:** both grants are re-read before and after every provider request, before CSV publication and around local append. Disabling either grant stops subsequent operations. This is a **local operator-managed marker**, not centrally enforced provider-token revocation or an atomic fence against a concurrent same-user process.
- **Failure:** provider, pagination, scope and grant errors produce no local append. A recovery CSV may remain if a later local append fails. A lost/invalid POST response is reported **unverified** and never automatically retried. Inspect the local log append history and private CSV before any manual replay.
- **Retention:** private recovery CSV persists until the operator applies their own retention/deletion policy. No background collection, secret storage, cloud upload, source write or employee performance scoring is enabled.

## Acceptance and limitations

The disposable suite tests/test_servicenow_incident_audit.py uses a fake ServiceNow provider but **actual loopback HTTP and OWG's event-log storage/append engine**. It exercises first append, replay deduplication, multiple pages, scope/field refusal, wrong target, revocation, truncation, DST and uncertain local append. These are synthetic integration tests, **not real ServiceNow/customer acceptance**. Native Ubuntu/macOS/Windows pytest, browser, accessibility, scale, packaging/release and current-main compatibility must still pass at the exact PR head before merging.

ServiceNow sys_audit availability, field coverage, ACLs, retention, audit table performance and timezone conventions vary by instance. Rows may be missing, deleted, inaccessible, delayed or shifted during offset pagination; even a short final page does not prove complete historical coverage. A customer-controlled sandbox with explicit permission and least-privilege credentials is required for real provider acceptance. No real-provider call or business write is authorized by this document.
