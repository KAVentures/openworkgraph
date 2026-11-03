# Microsoft Graph: operator-approved OWG append with durable receipt

This is a **local, explicitly approved** step after a complete bounded
`server.m365_outlook_delta` extraction. It uses the production
`m365_outlook_owg_receipt.append_verified` path to append to the selected,
authenticated OWG event log and independently read back event counts. It then
writes the existing resume-gate-compatible JSON receipt as an owner-private,
create-only artifact.

## Before starting

- Start the local OWG dashboard, select an existing CSV event log for the exact
  Microsoft 365 scope, and obtain the local dashboard session.
- Obtain the tenant/mailbox/folder-specific active grant and a complete candidate
  JSON plus its SHA-matched CSV from the bounded delta extractor.
- Review removals independently. A removal marker is **not** an automatic
  deletion instruction.
- Create an owner-private directory (0700 on POSIX). The receipt must be a new
  file named `receipt-*.json` in the same directory as candidate and CSV.
- Set `OWG_DASHBOARD_SESSION` locally; never paste a session or Graph token into
  command history, issue comments, or logs.

Run, with the actual scoped values and existing files:

```sh
python -m server.m365_outlook_verified_append_receipt \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER_ID \
  --grant-file /private/graph-grant.json \
  --candidate /private/round-2.json --csv-path /private/round-2.csv \
  --receipt-file /private/receipt-round-2.json \
  --log-id LOG_ID --confirm-log-id LOG_ID --port 8765 \
  --consent-local-read --consent-local-append
```

The module creates `receipt-round-2.json.attempt` **before** any append.
Once the attempt exists, it will not retry, even if the receipt was never
created. A successful receipt contains the production append's verified
inventory counts and CSV digest, and can be passed directly as
`--receipt-file` to `server.m365_outlook_resume_gate`.

Exit 0 means the receipt was durably persisted; exit 2 means refusal; exit 3
means a potentially committed append or uncertain local write. **Never remove
the attempt marker and blindly rerun.** For exit 3, inspect the authenticated
OWG append history, exact source event IDs, and live inventory before planning
manual recovery. An inventory count alone cannot prove individual event
membership. No provider checkpoint is advanced, no Graph requests are made,
no background sync is scheduled, and no provider acceptance is claimed.

The wrapper deliberately does not solve concurrent operators using different
receipt filenames for the same candidate. Operational policy must assign a
unique receipt path per candidate and prevent concurrent operators; stronger
cross-path server-side idempotency and event-level attestation remain open.
