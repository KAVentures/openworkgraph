# Microsoft 365 recovery triage (read-only)

Run after an uncertain append, or before initiating a new explicitly authorized extraction:

```sh
python -m server.m365_outlook_recovery_triage \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER \
  --grant-file PRIVATE_GRANT --candidate PRIVATE_CANDIDATE \
  --csv-path PRIVATE_CSV --receipt-file PRIVATE_RECEIPT \
  --log-id OWG_LOG_ID --confirm-log-id OWG_LOG_ID \
  --journal-dir PRIVATE_JOURNALS --handoff-dir PRIVATE_HANDOFFS \
  --consent-local-read --confirm-removals-reviewed
```

Set `OWG_DASHBOARD_SESSION` to the authorized local dashboard session. The command revalidates the current provider grant, candidate/CSV and receipt, then uses the authenticated OWG inventory readback. When both private journal and handoff are provided, it checks their candidate/log identity and journal digest. It never calls Microsoft Graph, writes an OWG event, retries a POST, updates a provider cursor or grants extraction permission.

Possible minimized outcomes: `receipt_verified` (operator may separately prepare handoff), `handoff_verified` (operator may separately authorize a new extraction), and `manual_reconciliation_required` (inspect authenticated OWG append history, current grant and private artifacts before doing anything). The latter returns exit code 3. Refusal, including missing consent, returns 2. No output contains Graph tokens, message contents, raw cursor URLs or individual event identifiers.

**Limits:** A receipt and matching inventory count are not event-level membership proof. This triage does not reconstruct a lost receipt or determine whether an uncertain POST committed. A new extraction still requires the existing independent consent gates. Real Microsoft Graph tenant acceptance, rate-limit/credential lifecycle and repeated-cycle customer validation remain outstanding.
