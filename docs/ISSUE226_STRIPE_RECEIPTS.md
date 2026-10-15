# Issue #226: Stripe-source invoice/refund receipts

This **local-only** pilot adds an authoritative *processor-state* status for
previously observed native Stripe dashboard invoice/refund objects. It is
disabled until an operator explicitly opts in.

Configured on the local service process only:

- OWG_STRIPE_SOURCE_READ_ENABLED=true
- OWG_STRIPE_ACCOUNT_ID=acct_...
- OWG_STRIPE_RESTRICTED_READ_KEY=rk_test_... or rk_live_...

The key MUST be a Stripe **restricted key** with only the read permissions
needed for Account, Invoices and Refunds. Never use a general sk_ key, paste
a key into ChatGPT, store it in a repository, or supply it via browser
events. The adapter intentionally refuses unrestricted secret keys.
Business production distribution will require Stripe Connect OAuth or
managed, customer-revocable restricted credentials plus a proper UI.

For a source read, the system verifies that the reference is present in the
user's retained canonical OWG evidence and has a trusted stored native
locator. It first GETs https://api.stripe.com/v1/account and requires an
exact match to the configured business account ID, then GETs exactly one
invoice or refund. No redirects, user-supplied hosts, writes, query strings,
raw source IDs, customer identity fields, amounts or payment credentials
can reach the AI output.

Recognized observed browser references:
- dashboard.stripe.com/invoices/in_...
- dashboard.stripe.com/test/invoices/in_...
- dashboard.stripe.com/refunds/re_...
- dashboard.stripe.com/test/refunds/re_...

No customer-hosted invoice URLs/receipt tokens are parsed.

Invoice state `paid` with `paid=true` may verify **Stripe's invoice payment
state**, not independent fulfillment or universal bank settlement. Refund
state `succeeded` may verify **Stripe's processor refund status**, but not
that the customer has already received funds in their bank account. Pending,
conflicting, unavailable and unknown states remain unverified.

No real billing account or secret was available for an end-to-end verification.
Synthetic tests validate auth defaults, response minimization, account
mismatch, identity mismatch and unknown/conflicting states. Actual API
permissions and customer-authorized retention/revocation tests must still be
completed before calling issue #226 production-ready.
