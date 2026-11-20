# Provider-event / agent-attempt verification (experimental primitive)

This module is a conservative **backend verification primitive**, not yet an authenticated REST endpoint or a deployed source connector. It must not be presented as an end-to-end verified outcome feature until those are implemented.

The caller supplies a fresh, independently authenticated, tenant-scoped provider read, with a complete bounded event set, exact account/object identity and a provider-generated event ID plus correlation key that was actually transmitted with the agent's operation. The matcher rejects incomplete, stale, conflicting, mismatched, revoked and unavailable evidence. It never infers identity from temporal proximity or operator claims.

Even an exact successful provider operation returns `customer_outcome: unknown`. A later, separately authorized source check is required to verify the declared business goal. The current module does not create grants, store secrets, call providers, write data, persist evidence, or modify existing REST/MCP contracts.

Next vertical milestone: integrate with a real provider's authenticated event-read adapter, durable scoped receipt/correction storage, and an authorized REST route, after claiming those shared files. Test native cross-platform CI and live test-account behavior before claiming production acceptance.

When a production caller supplies `recheck_authorization`, the callback must confirm the **current** scoped grant/consent/source entitlement before and after the provider read. Revocation or an unavailable grant store yields `unknown`. This hook does not itself authorize any read; the calling authenticated route must enforce scope and retention and recheck before storage or disclosure. Never expose the unguarded primitive as an endpoint.
