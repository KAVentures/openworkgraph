# Explicit Stripe attempt event verification (operator CLI)

Run this only after an operator has independently authorized a restricted,
read-only Stripe credential for the exact account. This command makes **no**
network request without `--confirm-provider-read`. It never writes to Stripe.

```sh
export OWG_STRIPE_ATTEMPT_READ_KEY='rk_test_...'
python -m server.open_work_stripe_attempt_cli \
  --account acct_example --object-id in_example \
  --event-type invoice.payment_succeeded \
  --idempotency-key attempt_example --event-id evt_example \
  --confirm-provider-read
unset OWG_STRIPE_ATTEMPT_READ_KEY
```

The event ID must come from an independently identified Stripe event, not
merely the agent's success message. The adapter fetches the authenticated
account and a complete, bounded Events page, and checks exact object, event
type, request idempotency key and event ID. A matching event means
`verified_provider_event` **only**; provider-operation completion and actual
customer/business outcome remain `unknown`. The command prints only a
bounded verdict, not source objects or credentials. Exit 0 means event
identity verified; exit 3 means unknown/refused.

Limitations: Stripe Events retention, page limits, missing idempotency keys,
or incomplete history can produce unknown. This is an explicit local operator
tool, **not** an authorized multi-tenant REST endpoint, durable case receipt,
agent execution permission, or independent proof of causality. Production
integration still requires actor/grant-scoped authorization, revocation and
retention, append-only correction history, and real restricted-account
acceptance. Mocked tests do not establish live-provider acceptance.


## Opt-in durable local recheck journal (experimental)

Supply `--journal-path /private/path/receipts.sqlite --tenant TENANT --actor ACTOR --attempt RUN` together with the normal explicit provider-read flags to append a scoped local receipt after each check. Without all four flags, no receipt is written. Rechecks append history rather than overwriting a prior result. Use `--purge-journal-scope` with the same journal, tenant and actor arguments to delete that scope on revocation or retention expiry (the other required identity arguments remain mandatory for this CLI). Never store Stripe credentials or customer payloads. A provider event does not prove operation completion or customer outcome. Journal path and scope must be controlled by the local operator; this experimental CLI is not a tenant-authenticated hosted API and does not implement automatic retention or grant checks. Do not enable this for shared/multi-user installations without an authenticated storage boundary.


### Explicit local receipt retention

Operators may call `purge_expired_receipts(path, tenant=..., actor=..., older_than_days=30)` from `server.open_work_attempt_receipts` to delete only older journal rows for that exact tenant/actor. The operation requires an explicit durable path, a 1–3650 day retention window and uses UTC timestamps. It does not schedule itself, authorize provider reads, revoke credentials, or replace full-scope deletion on consent withdrawal. A journal entry remains evidence of a prior check, not proof of customer completion.
