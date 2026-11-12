# Microsoft 365 reviewed operator transaction (Worker E, R3/R20)

The opt-in Microsoft Graph extraction and offline candidate review remain **separate**
operations. After inspecting the candidate's event count, removal markers and
SHA-256 digest, an operator may invoke the following explicit, non-retrying
transaction to append, verify a handoff, and commit the scoped checkpoint:

```sh
python -m server.m365_outlook_reviewed_transaction \
  --tenant "$TENANT" --mailbox "$MAILBOX" --folder "$FOLDER" \
  --log-id "$LOG_ID" --confirm-log-id "$LOG_ID" \
  --grant-file "$GRANT" --candidate "$CANDIDATE" --csv-path "$CSV" \
  --receipt-file "$RECEIPT" --journal-dir "$JOURNAL" \
  --handoff-dir "$HANDOFF" --ledger-dir "$LEDGER" \
  --successor-label round-2 \
  --expected-review-csv-sha256 "$REVIEWED_SHA256" \
  --expected-previous-candidate-sha256 "$PREVIOUS_SHA256" \
  --consent-local-read --consent-local-append \
  --confirm-candidate-reviewed --confirm-removals-reviewed \
  --confirm-checkpoint-commit
```

For the initial generation omit `--expected-previous-candidate-sha256`.
Provide the local dashboard session using `OWG_DASHBOARD_SESSION`, and the
mandatory audit destination, when configured, using `OWG_M365_AUDIT_DIR`.
Graph credentials must not be supplied to this command. It does not contact
Graph or initiate new extraction; the production delegates revalidate the
current grant, CSV digest, authenticated OWG append/readback and checkpoint.

**Recovery:** A nonzero result is `manual_reconciliation_required`, not
permission to repeat the transaction. Inspect the receipt, append history,
event membership, handoff and checkpoint with the existing operator-cycle
commands before any new write. A crash after append can leave events written
without a checkpoint. An audit failure after a write likewise requires manual
reconciliation. No removal is automatically applied. This is a CLI integration
increment, **not** dashboard UX completion or real Microsoft tenant acceptance.

**Pre-append generation guard:** Before any OWG append, the transaction now reads the current scoped private checkpoint under the live grant. For generation one, the ledger must be empty; for later generations, `--expected-previous-candidate-sha256` must exactly match the latest committed predecessor and its destination log. A missing, revoked or expired grant, stale generation, or different destination refuses before appending. This is an early safety check, **not** a transaction lock: the checkpoint commit still performs its authoritative exclusive-create generation check. If another operator advances the ledger after this preflight, append may already have happened when commit refuses; inspect membership and receipts and reconcile manually rather than rerunning blindly.

**Audited handoff boundary:** With `OWG_M365_AUDIT_DIR` configured, a successful verified handoff now requires a private, minimized `handoff` phase receipt before the checkpoint can advance. An audit-write failure after append stops checkpoint commit and requires manual reconciliation; it never triggers a retry. Review, append, handoff, and commit receipts are distinct. The audit directory must be private and supported by the POSIX writer.

**Read-only preflight before approval:** The same command supports
`--preflight-only --consent-local-read` without append/commit approvals.
It checks the scoped live grant, candidate CSV digest, destination log and
checkpoint predecessor and reports the proposed next generation. It makes
zero Graph requests, OWG writes or checkpoint changes. Only scope, grant, candidate, ledger and reviewed digest arguments are required\nfor this read-only mode; append receipt, journal, handoff and successor\narguments are required only for mutation. A rejected preflight reports\n`preflight_rejected` (not `manual_reconciliation_required`) because this\nmode cannot append or commit. This is a point-in-time review, not
a lock or authorization to commit: the later mutation revalidates the grant,
candidate and checkpoint and still requires all explicit approvals.


Minimal read-only command (no append destination paths or mutation approvals):

```sh
python -m server.m365_outlook_reviewed_transaction \\
  --preflight-only --consent-local-read \\
  --tenant "$TENANT" --mailbox "$MAILBOX" --folder "$FOLDER" \\
  --log-id "$LOG_ID" --confirm-log-id "$LOG_ID" \\
  --grant-file "$GRANT" --candidate "$CANDIDATE" --ledger-dir "$LEDGER" \\
  --expected-review-csv-sha256 "$REVIEWED_SHA256"
```

For later generations, add `--expected-previous-candidate-sha256`.
This is still local reviewed-candidate preflight, **not** an authenticated
Graph extraction, dashboard integration, or real-tenant validation.
