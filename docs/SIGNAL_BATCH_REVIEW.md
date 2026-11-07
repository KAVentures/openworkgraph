# Local signal inbox: batch review (R10)

The **Process mining → Signals → Review evaluated signals** inbox supports an
explicit multi-select acknowledgement. Select up to **20 unreviewed** signals and
confirm once. Acknowledge remains a **local, descriptive review**: it never
confirms a business outcome, dispatches an agent, delivers notifications,
changes source systems, or grants any execution permission.

## API

`POST /v1/event-logs/{log_id}/signal-inbox/acknowledge-batch`

Requires a current **dashboard session**, not a normal AI/API bearer token.

```json
{
  "reviews": [
    {"rule_id": "sig_0123456789abcdef", "state_fingerprint": "<64-lowercase-hex-digest>"}
  ]
}
```

Fingerprints must come from
`GET /v1/event-logs/{log_id}/signal-inbox` **after evaluating** the saved
rules. Request keys and item keys are exact; 1–20 unique rule IDs are required.
The response contains `reviewed` and the refreshed inbox fields, including
`items`, `unreviewed`, `actions_authorized: false`,
`delivery_enabled: false`.

This batch operates under the existing local event-log lock and one DuckDB
transaction. **Every** rule must still exist on the same log and have a
triggered, evaluated, dataset-current, fingerprint-matching state. Active
snoozes require an explicit resume. If any item fails, the full batch is
rejected with **no partial acknowledgements**. Retried exact acknowledgements
keep their original review times.

A changed import/append basis must be checked again and selected anew; no
fingerprint bypass, client-claimed currentness, implicit wake-up of snoozed
results, or role escalation is permitted. Deleting the event log deletes its
reviews using existing local retention semantics.

## Validation

`tests/test_signal_bulk_review.py` covers rollback on stale items, changed
dataset basis, snoozed states, duplicate/invalid payloads, cross-log rejection,
idempotent timestamps, dashboard auth, and dashboard UI wiring. This extension
does not change scheduled evaluation, existing single-signal review, other
signal rules, or outbound sharing policies.
