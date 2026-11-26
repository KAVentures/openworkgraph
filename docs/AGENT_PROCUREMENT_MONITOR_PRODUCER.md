# Scoped local monitoring producers

A native local adapter may submit observations to one retained monitoring
journal, one frozen window, and one allocated arm. This is a separate
write-only bearer, not the general API token or a dashboard session.
The operator provisions and revokes credentials using the dashboard.
No provider or deployment is contacted by OWG.

## Provision

First retain a verified monitor snapshot with router-journal-append.
Older retained journals need one new verified checkpoint to retain their
validated plan. Use router-producer-grant with the existing JSON-text
dashboard envelope and workflow_id/revision query parameters. Document:
monitor_id, expected_head_sha256, producer_alias, window_id, arm,
ttl_seconds (60 to 604800), confirmed=true. The response includes the
opaque bearer exactly once. Give it only to the authorized native local
producer through an appropriate local secret store. Never log it or place
it in a URL. Database storage contains a SHA-256 token digest, not the
bearer. Grant metadata and audit history contain no token.

## Submit

POST /v1/process-catalog/procurement-producer/ingest on the local secure API,
Authorization: Bearer <producer token>, Content-Type: application/json.
No browser Origin header. Submit a JSON object with format
openworkgraph.procurement-monitor-producer-batch.v1, batch_id (opaque alias),
and observations (1–100 exact pilot observation objects). No journal scope
or cutoff is supplied by the producer. The 512 KiB body limit applies to
streamed requests. The operator distributes only the assigned slot/case
tokens outside this API; the credential has no read or discovery rights.

Only assigned window/arm slots, matching case tokens and in-window timestamps
not after the server clock are accepted. Wrong actual version or destination
is retained as adverse evidence rather than discarded. Every batch is
atomic; duplicate slot/source references, changed observations, expired
or revoked grants and stale process revisions are rejected. An exact
batch-ID replay under the same active grant is idempotent (same normalized
observations); changed payload conflicts. The receipt exposes only the
grant ID, batch ID and accepted count, never the journal head, other arm,
prior observations or report. The server clock must advance past the last
retained cutoff; retry a 409 clock conflict only after it advances.

Accepted observations automatically trigger a cumulative checkpoint review
inside the same write transaction. Sticky historical incidents continue to
block continuation. Existing manual imports remain declarations; only
observations first accepted by this endpoint receive producer attribution.
A producer bearer authenticates possession of a provisioned secret, not
independent business truth, an executed model artifact or provider identity.

## Revoke and inspect

router-producer-revoke: monitor_id, expected_head_sha256, grant_id,
confirmed=true. Revocation is serialized with ingest; previously committed
evidence remains. Journal detail lists grant metadata and observation hashes
with producer attribution, not tokens. Explicit journal removal deletes its
grants together with its history. Limits: 48 grants per journal (including
revoked/expired), 100 observations per batch, existing bounded journal
capacity. No automatic pruning or silent evidence rewrite. Local database
administrators remain trusted. No scheduler, remote collector or deployment
authority is created; automated evaluation occurs on accepted submissions.
