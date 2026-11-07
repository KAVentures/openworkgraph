# Authenticated handoff freshness preflight (R5)

In the Gateway **Shared processes** page, sign in through the existing delegated organization identity, open a shared process, and select an exported OWG evidence-handoff-review.v1 JSON packet. The new **Check latest authorized revision** action reads the current process via the existing authenticated `GET /v1/human/processes/{workflow_id}` endpoint. The packet itself is never sent. The UI refuses oversized, malformed, non-review-only, cross-workflow, archived, historical, stale-revision and SHA-256-mismatched packets, and fails closed on denied/expired access.

A successful check means only that the authorized Gateway read returned the same latest process revision and content digest as the packet. It is **not** proof of current consent, process-owner approval, source authority, business outcome, retention validity or execution permission. No handoff, source read, agent run, write, grant or approval is performed.

Changing the packet, selecting another process or signing out clears the result. In-flight responses are discarded if the selection or identity changes. The Gateway still independently enforces tenant, role and revocation boundaries. This is an incremental UI slice of #370 R5, not complete end-to-end execution authorization or real-customer acceptance.

Acceptance still needed: actual authenticated browser journey with a consenting organization, 401/403 and concurrent revision drift; full native six-workflow CI; independent review and pilot.
