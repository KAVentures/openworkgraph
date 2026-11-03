# Microsoft Graph next-round handoff

This command completes the **local operator handoff** between an already verified Graph delta import and a separately initiated next extraction. It does not perform provider I/O, store delta tokens, advance Graph checkpoints, or launch a sync. It adds an independently retained, owner-private manifest tied to the existing operator decision journal.

Prerequisites: consented per-folder Graph export, successful OWG append with verified receipt, current local dashboard session, reviewed removals, and an existing owner-private journal directory and a **different** owner-private handoff directory. Set `OWG_DASHBOARD_SESSION` to the authorized local dashboard session.

```sh
python -m server.m365_outlook_resume_handoff \
  --tenant reviewed-tenant --mailbox reviewed-mailbox --folder AQMkSelectedFolder \
  --grant-file /private/owg/grant.json \
  --candidate /private/owg/round-001.json \
  --csv-path /private/owg/round-001.csv \
  --receipt-file /private/owg/round-001-receipt.json \
  --journal-dir /private/owg/resume-journal \
  --handoff-dir /private/owg/resume-handoffs \
  --successor-label round-002 \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --consent-local-read --confirm-removals-reviewed
```

The command re-runs the production resume gate through `record_resume`: current grant, candidate digest, append receipt, explicit removal review and authenticated OWG inventory must still match. It then independently reads the private journal and creates a manifest bound to the journal's SHA-256 digest, source CSV digest, target log and chosen successor label. A second identical invocation revalidates live state and returns `idempotent_replay=true`; a conflicting or corrupted handoff fails closed. Exclusive creation and file/directory sync avoid silently overwriting uncertain writes. Retain or delete these local artifacts only under the organization's approved retention process.

**Operator next step:** inspect the handoff manifest, then explicitly start the next Graph delta extraction using the existing CLI and its `--confirm-previous-import` safeguard. The manifest is not an access token or authorization to bypass that safeguard. It is **not** event-level cryptographic attestation, automatic checkpoint commit, proof of provider continuity, or real Microsoft Graph tenant acceptance. Do not place identifiers, access tokens or PII in successor labels.

Acceptance requires native Ubuntu/macOS/Windows pytest, all six exact-head workflows, browser, process collaboration, scale, accessibility, starter and packaging/release checks before merge. Focused tests cover durable persistence, idempotent replay, changed/revoked gate refusal, journal mismatch, conflicting handoff and directory isolation. Authorized real-provider testing remains outstanding.
