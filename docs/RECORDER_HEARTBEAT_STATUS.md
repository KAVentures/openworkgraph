# Hosted recorder health: explicit device heartbeat

The hosted connector can **never** infer whether the local recorder is running
from the time of the last synchronized work event. Pauses, offline mode,
network errors, OS permission loss and ordinary inactivity all produce similar
gaps in event history.

This optional, minimal-data health protocol distinguishes a fresh attested
collector heartbeat from stale or absent evidence:

1. The local API collects native collector heartbeats in memory at `/v1/heartbeat`.
   The new `GET /v1/collector-health` reports only (a) whether a native
   collector heartbeat was received within 45 seconds and (b) the local capture
   policy state (recording/paused/stopped). It never includes titles, metadata,
   work text, identity, session IDs or page URLs.
2. While Gateway evidence sharing is enabled and unpaused, the sync worker
   polls this **fixed loopback endpoint** from a separate HTTP client that
   does NOT include its remote Gateway Authorization header. Every 20 seconds,
   it posts only the small state object to authenticated
   `POST /v1/device/recorder-heartbeat`.
3. The Gateway accepts status only from a valid enrolled device token with
   `evidence:write` and scopes the row to token-authorized organization,
   actor and device. Only the most recent state per device is stored, with no
   work contents. A device is allowed to report 'recording' only with a recent
   collector heartbeat.
4. The public `get_current_work_context.capture_status` exposes the latest
   actor-scoped report as recording/paused/stopped/no_recent_collector_heartbeat
   **only if the device report is at most 60 seconds old**. Stale or absent
   reports return `unknown_to_hosted_connector`, NOT `stopped`.
5. Device status is untrusted for remote policy/authorization: it is evidence
   of what an enrolled device recently reported, not unforgeable hardware
   attestation or proof that every browser extension and native agent sensor
   is healthy.

Capture authorization stays unchanged. Pausing Gateway sharing sends no
heartbeat. Unavailability of this optional health endpoint must not block
normal consented evidence synchronization. Old Gateway versions may return
404 to this health report; the worker handles it independently.

**Deployment:** update the Gateway and hosted MCP to add the status table and
read path, then update desktop clients to emit health reports. Until all
components are deployed, state will remain unknown. Health reporting does not
migrate or delete any historical evidence; do the legacy cloud-data cleanup
only after device-first redaction is actually active.
