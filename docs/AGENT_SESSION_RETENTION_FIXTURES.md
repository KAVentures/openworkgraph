# Native agent-session cursor tests: retention-safe fixtures

Tracking: [#493](https://github.com/KAVentures/openworkgraph/issues/493). This is a test-only correction; it does not change production privacy or source capture.

## Why the previous fixture failed

Three native-session cursor tests used fixed 2026-09-30 timestamps. On 2026-10-30, the real local agent-session store's default 30-day message retention deletes their first message during a subsequent sensor scan. This makes the tests fail for the wrong reason even though the cursor still advances and the later message is observed. Re-running or extending retention would hide the real problem.

The affected synthetic tests now capture **one UTC clock reading per test**, deliberately one day in the past, and derive later message timestamps using fixed one-second offsets. Their multi-scan ordering, new-only bootstrap, disabled-interval exclusion, oversized-record skip, and file-replacement assertions remain unchanged. The one-day margin is not a production policy override; it only keeps these synthetic observations recent during cross-platform CI.

## Explicit retention boundary

`test_local_visible_message_retention_exact_cutoff_is_idempotent` freezes **only the local store's clock** at 2026-10-30 12:00:00 UTC, leaves the policy at **30 days**, and inserts three visible messages:

| Observed UTC | Expected after cleanup |
| --- | --- |
| 2026-09-30 11:59:59 | Deleted (strictly before cutoff) |
| 2026-09-30 12:00:00 | Retained (exact cutoff) |
| 2026-09-30 12:00:01 | Retained (after cutoff) |

It verifies the real store cleanup path deletes one row, updates the session count to two, and is idempotent on a second cleanup. No mocked deletion SQL, disabled retention, extended timeouts, or skipped assertions.

## Acceptance and limitations

Run `python -m pytest -q tests/test_agent_session_continuity_v0109.py` and the required exact-head Windows/macOS/Linux, browser/accessibility, scale, starter, packaging and release workflows. Until those finish successfully this change is **not** CI-validated. The tests use synthetic local sessions, not live Entra/Okta/ServiceNow, customer consent or production deployment evidence. No source, credential, SCIM, event-log, gateway, sensor or retention implementation files are modified.
