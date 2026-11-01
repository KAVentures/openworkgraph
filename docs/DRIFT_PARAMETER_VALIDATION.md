# Strict concept-drift window parameter validation (R6)

The saved event-log drift analyzer compares adjacent windows of recorded case paths. Its public REST route already declares typed, bounded integer query parameters, but the underlying `drift.drift()` function can also be called directly by internal integrations and tests.

Previously, `int(window_cases)` and `int(min_cases)` ran before validation. A fractional value such as `20.5` could silently become `20`; a malformed string, missing value, or nonfinite float could instead raise a raw `ValueError`, `TypeError`, or `OverflowError`. That was inconsistent with the analyzer's `EventLogWorkspaceError` validation contract and could mask invalid settings.

The analyzer now requires genuine Python integers (not booleans) for both settings:

- `window_cases`: inclusive 20–100,000.
- `min_cases`: inclusive 10–10,000.

All invalid types and out-of-range values raise `EventLogWorkspaceError` **before querying any saved log**. The HTTP route still accepts ordinary integer query strings through its existing FastAPI integer parser; its endpoint, query names, response schema, error handling and authorization are unchanged. Existing valid integer inputs and the drift calculation, sampling, correction, case evidence and display are unchanged.

Synthetic parameterized tests exercise fractional, nonfinite, string, null, boolean and container values, verify early rejection, and verify the inclusive integer bounds without reading a log. These are **not** native cross-platform CI results, real-provider validation, consenting-customer acceptance, or proof of business outcomes. No event data, consent, retention, tenant access, uploads, agent authorization or business writes change.
