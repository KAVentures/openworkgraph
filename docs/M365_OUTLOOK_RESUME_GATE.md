# Microsoft Graph delta resume authorization (R3)

This is an **operator-controlled local gate**, not an automated Graph cursor commit.
Run the existing Graph delta extraction and OWG append workflow first. Capture the
single JSON response from `python -m server.m365_outlook_owg_receipt` in a
private, owner-only `receipt.json` beside the candidate and CSV; do not
copy secrets or source text into the receipt.

After independently reviewing all removal markers, run:

```sh
python -m server.m365_outlook_resume_gate \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER_ID \
  --grant-file /private/grant.json \
  --candidate /private/candidate.json --csv-path /private/events.csv \
  --receipt-file /private/receipt.json \
  --log-id LOG_ID --confirm-log-id LOG_ID --port 8765 \
  --consent-local-read --confirm-removals-reviewed
```

Supply the OWG local dashboard session in `OWG_DASHBOARD_SESSION`.
A successful response means the candidate/CSV SHA, exact destination, counts,
current authenticated inventory and grant were consistent **at the time of
the check**. It is not event-level proof, provider tenant verification, or
permission to delete events. A count can coincide with a different set of
events. Concurrent changes after verification remain possible.

Only after successful review may the operator explicitly pass the original
candidate to `server.m365_outlook_delta` with
`--resume-candidate /private/candidate.json --confirm-previous-import`.
That command still requires fresh Graph read/export consents and a current
provider grant. The gate never contacts Graph, advances a checkpoint, retries
an uncertain append, or deletes records. If inventory has changed, the
receipt is uncertain, the grant is revoked, or removals have not been reviewed,
stop and reconcile manually. Never manufacture a receipt to bypass review.

**Limitations:** The receipt is an operator-supplied JSON document, not a
cryptographically signed event-level import attestation. This is a bounded
local safety check; production-grade replay prevention and authoritative
event membership receipts remain future work. Real provider sandbox
acceptance is still required.
