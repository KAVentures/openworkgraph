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
