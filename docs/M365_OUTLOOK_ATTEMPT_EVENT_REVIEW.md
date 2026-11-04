# Microsoft Graph uncertain append: private event review

After an uncertain OWG append, the attempt latch must remain in place. An inventory count is not evidence that a particular source event committed. This operator command prepares the **exact opaque source-event IDs** from the previously reviewed, SHA-bound CSV for independent event-level membership investigation. It **does not** query individual OWG event identities, reconcile them, permit retries, or advance a Graph cursor.

Run from the repository root with an authenticated local dashboard session in `OWG_DASHBOARD_SESSION`:

```sh
python -m server.m365_outlook_attempt_event_review \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER \
  --grant-file PRIVATE/grant.json \
  --candidate PRIVATE/candidate.json --csv-path PRIVATE/events.csv \
  --receipt-file PRIVATE/receipt-1.json --output PRIVATE/review-1.json \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --consent-local-read --consent-private-export
```

Use the **actual** paths and log ID from the prior operator run. The output must be in the same private directory as the candidate, CSV and receipt. It is exclusively created with owner-only permissions; existing files are never overwritten. The command verifies the current provider grant, candidate and CSV digest, exact latch and target namespace, and live authenticated OWG inventory. It makes no Microsoft Graph requests and no OWG write requests.

The private JSON contains opaque event IDs and content digests; protect it as retained source-derived data. Standard output contains only status and count, never mailbox, cursor or event IDs. If authorization changes or artifacts differ, discard the result and investigate. A successful manifest is **not** proof of membership or authority to replay an uncertain POST. Only a separate, independently verified event-level reconciliation workflow could authorize further recovery. Never delete the latch to force a retry.

Acceptance is limited to this local operator workflow and synthetic tests. Customer-controlled Graph sandbox and actual OWG event-level matching remain separate outstanding gates.
