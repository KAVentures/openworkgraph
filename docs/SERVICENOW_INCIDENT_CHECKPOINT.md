# ServiceNow selected incidents: operator-controlled incremental checkpoint

This optional CLI builds on the existing **selected-record** ServiceNow HTTPS reader, durable SQLite journal and authenticated local OWG event-log append. It is a real bounded read/append workflow, not a fake-only adapter, scheduled service, enterprise authorization server or complete ServiceNow change feed. No source write is performed. It is disabled unless the operator explicitly invokes it and supplies all four consents.

## Before running

1. Follow `docs/SERVICENOW_INCIDENT_RUN.md` to create an owner-only grant file for the exact ServiceNow `*.service-now.com` host, local tenant and 1–8 selected literal 32-character incident `sys_id` values. Place the journal, checkpoint and recovery CSV under owner-only private directories. On POSIX, files are mode 0600. Use an operator-approved instance IANA timezone; the provider-local timestamp zone is **not verified by ServiceNow**.
2. Obtain an approved read-only ServiceNow bearer token via your existing credential process, and set `OWG_SERVICENOW_TOKEN` only in the current environment. Obtain an authenticated OWG dashboard session and set `OWG_DASHBOARD_SESSION`. Do not commit, log or paste credentials. This CLI does not create or manage them.
3. Create an existing local OWG CSV event log with exactly the namespace described in `docs/SERVICENOW_OWG_APPEND.md`; copy its log ID. The command refuses a mismatched log **before** the provider GET. Explicitly confirm the same ID twice. OWG append is a local write; no external business write is authorized.
4. Choose a **provider-local** initial lower bound. The first run requires `--initial-since`; the bound cannot later be changed without starting a new checkpoint/journal review. This is a selected snapshot observation, not proof that older incidents or intermediate revisions were extracted.

Example, replacing all placeholders with approved local values (repeat `--incident-id` up to eight times):

```sh
python -m server.servicenow_incident_checkpoint \
  --instance sample.service-now.com --tenant approved-local-tenant \
  --incident-id 0123456789abcdef0123456789abcdef \
  --grant-file /private/owg/snow-grant.json --timezone UTC \
  --journal /private/owg/incident-journal.sqlite \
  --checkpoint /private/owg/incident-checkpoint.json \
  --output /private/owg/incident-recovery-001.csv \
  --initial-since '2026-10-20 00:00:00' \
  --log-id EXISTING_OWG_LOG_ID --confirm-log-id EXISTING_OWG_LOG_ID \
  --consent-selected-read --consent-local-journal-read \
  --consent-local-append --consent-incremental-checkpoint
```

On subsequent runs, use a **new** `--output` path and omit `--initial-since`. The checkpoint is bound to the exact selected host/tenant/IDs/timezone, journal absolute path and local destination log ID. A different binding, invalid/corrupt file, concurrent or stale `.lock`, changed initial bound, revoked grant, output collision or wrong OWG namespace refuses the operation. Do not automatically remove a stale lock until investigating whether another process is still running. The command is operator-invoked; it does not schedule itself.

## What advances, and when

Each successful invocation performs one bounded selected HTTPS GET with the existing TLS/host/response restrictions, persists revisions in the private journal, publishes a new private recovery CSV, and appends that journal through authenticated loopback OWG HTTP. Only after a **verified successful** append and current grant check does it atomically write the private checkpoint. A failed or uncertain append **does not advance** the checkpoint. If the local append succeeded but checkpoint publication failed, the CLI reports `append_completed_checkpoint_unverified`; inspect OWG and the journal before retrying. Existing source-key deduplication makes deliberate replays safe, but the CLI does not automatically retry an uncertain POST.

The checkpoint stores only a SHA-256 binding digest, initial lower bound and the latest journaled UTC observation time. It contains no raw incident IDs, token, session or private descriptions. On later runs, the query lower bound is the **latest successfully appended observation minus 48 hours**, converted to the declared source-local timezone, but never earlier than the initial lower bound. This inclusive replay window is a practical overlap against ordinary delayed observations and timestamp ambiguity; it is **not a completeness guarantee**. Older late-arriving changes, provider-side history between polls, ACL-filtered rows, source clocks, out-of-order records, deleted incidents, and retention beyond the 4096-event/8 MiB journal limit remain unknown or unsupported. No provider server-side cursor or high-watermark attestation exists.

The source journal is not automatically pruned: the command fails closed at its size cap. The checkpoint is not a central revocation fence, and grant-file changes by the same OS user are not an enterprise-grade identity/lease system. The operator must configure and enforce local retention/deletion policies separately, and must not use a checkpoint as evidence that a real customer IdP or ServiceNow tenant has been validated.

## Acceptance and recovery

`tests/test_servicenow_incident_checkpoint.py` uses synthetic ServiceNow HTTPS responses and a disposable **actual local OWG HTTP event-log append engine**, checking initial and overlapping follow-up reads, revision dedupe, checkpoint persistence, no provider GET on missing consent/scope/lock/invalid bound, uncertain POST, and post-append checkpoint failure. No real provider or customer data is contacted. Full native cross-platform pytest, browser, process-collaboration, scale, accessibility, starter and packaging/release CI plus authorized customer-controlled ServiceNow acceptance are separate gates before rollout.
