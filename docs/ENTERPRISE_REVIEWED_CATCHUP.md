# Reviewed advancing-window catch-up

Refs #1572, #1611, #1615 and #1619. The local enterprise dashboard can review the next exact advancing request window for up to twenty profiles and execute a single explicitly reviewed window at a time. This uses the existing retained ingestion engine, not a second store, scheduler or cursor.

## Workflow

1. Load profiles and select the intended sources in Reviewed catch-up queue.
2. Review exact UTC start/end bounds, current checkpoint, configured overlap, existing exclusion count, readiness and estimated backlog. The list sorts eligible profiles by approximate outstanding request time; estimates do not establish throughput, completeness or live source availability.
3. For one eligible profile, explicitly approve the exact window, the provider read, local retention and reconciliation of competing workers/schedules.
4. Execute the reviewed window. Inspect the committed checkpoint, added and duplicate counts. Refresh and review again before another dispatch.

Preview does not contact providers or refresh credentials. It uses local binding checks, existing provider cutoff calculations and current local state. Disabled feeds, integrity holds, active or expired intents, cooldowns, missing retained datasets and unavailable local bindings block eligibility. No lease is taken by preview. Expired routine intent handling in ordinary ingestion is unchanged.

A plan is short lived (ten minutes) and authenticated using a domain-separated profile-local key. The server verifies the reviewed interval and exact private profile state under the workspace lock before creating the normal lease and run journal entry. The current provider cutoff must still permit the whole reviewed interval; the engine does not widen it if more recent data becomes available. A stale, modified, expired or no-longer-eligible review is refused before a provider read. Historical recovery remains a separate reviewed workflow. The normal retention exclusions, deduplication, checkpoint, committed request coverage and transactional semantics remain in force.

A review authorizes no batch dispatch, automatic retry or schedule modification. Separate workers may run outside this flow, so stop/reconcile competing jobs as appropriate. After an interrupted response or failure, inspect existing history instead of assuming no work occurred; create a fresh review before retrying. This is not a guarantee of upstream event completeness or remote cancellation.

## API

Human dashboard session required. GET /v1/enterprise-connectors/catchup lists profiles. POST /v1/enterprise-connectors/catchup/preview accepts a bounded list of profile IDs and revisions. POST /v1/enterprise-connectors/catchup/execute requires the reviewed profile ID, revision, review timestamp, exact start/end and authenticated plan digest plus four separate Boolean confirmations. The execute response is a local committed result, not a provider health attestation.

## Validation

Regression fixtures cover passive preview, exact frozen bounds, real retention and coverage, competing dispatch, stale or tampered approval, expiry, provider cutoff changes, cooldown, binding failure, holds, interrupted intents and failed read handling. The browser acceptance path uses an actual local SQLite source through the new queue. The enterprise-stack acceptance workflow includes the new tests. Tests have not been executed locally; CI and live deployment acceptance remain required.
