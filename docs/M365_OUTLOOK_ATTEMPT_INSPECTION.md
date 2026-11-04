# Microsoft Graph uncertain append: read-only inspection

This command is for an operator whose previous `m365_outlook_verified_append_receipt` invocation left a `receipt-*.json.attempt` latch and no durable receipt. It **does not reconcile event identities**. A count from the authenticated OWG inventory is insufficient to establish that any particular event was committed.

## Operator workflow

1. Keep the existing latch, candidate, CSV and grant private. Do not remove or rename the latch or repeat the append POST.
2. Verify that the intended tenant/mailbox/folder and destination log are correct. Ensure the scoped grant remains active.
3. With a fresh dashboard session and explicit permission to inspect local state, run:

```bash
OWG_DASHBOARD_SESSION='<local-session>' python -m server.m365_outlook_attempt_inspection \
  --tenant '<tenant>' --mailbox '<mailbox>' --folder '<folder-id>' \
  --grant-file /private/grant.json \
  --receipt-file /private/receipt-example.json \
  --log-id '<log-id>' --confirm-log-id '<log-id>' \
  --consent-local-read
```

The response returns a minimized `inventory_events_now` count and `status: indeterminate`; `safe_to_retry` remains false. The production inventory delegate performs an authenticated local GET, with the source grant checked before and after. It performs no Graph request, OWG POST, provider checkpoint update or local write.

A missing or malformed latch, mismatched target, revoked grant, ambiguous inventory, or already existing receipt refuses. If a receipt exists, use the normal verified-receipt/resume flow rather than this command.

## Safety and limits

Even when the inventory count increased, other writers, duplicate suppression and partial commits make count-based attribution unsafe. A count that has not increased also does not prove a POST never committed. Preserve the attempt latch until independent event-ID reconciliation and authorized recovery are available. This tool is a triage capability, not event-level attestation, a retry authorization, a production credential manager, or real Microsoft Graph tenant acceptance.

Run focused tests with `python -m pytest -q tests/test_m365_outlook_attempt_inspection.py`; require the complete exact-head CI suite before merge.
