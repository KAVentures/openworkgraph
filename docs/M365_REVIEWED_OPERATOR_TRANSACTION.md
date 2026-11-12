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
