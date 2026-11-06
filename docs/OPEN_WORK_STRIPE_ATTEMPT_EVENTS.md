# Stripe event source adapter for agent-attempt evidence

Experimental, explicitly invoked read-only Stripe API adapter for #247 / PR #675.

The caller supplies a separately authorized Stripe restricted credential with permission to read the account and events, and the expected account, object, event type, and idempotency key transmitted with the agent's request. The adapter independently reads the Stripe account and one bounded event page; a paginated/incomplete response fails closed. A matching event can be passed to the conservative attempt matcher using the provider-generated event ID. The adapter never executes writes or runs by default.

**Limits:** Stripe may not retain or expose the required event/idempotency key, and a complete first page is not a proof of complete historical retention. Event emission establishes an observed provider event, not necessarily agent causality (if the correlation key was not independently bound to an authorized attempt), invoice payment, customer completion, or a declared business outcome. No persistent receipts, actor authorization, route registration, tenant grant lifecycle, live-account acceptance or cross-ledger atomicity is supplied here. These remain mandatory before the vertical feature can be merged as production ready. Avoid storing tokens or customer event payloads in logs.

A matching Stripe event now yields `verified_provider_event` with `provider_operation: unknown`, never `succeeded` merely because Stripe emitted an event. A separate independently sourced operation-state check is necessary before reporting operation success.
