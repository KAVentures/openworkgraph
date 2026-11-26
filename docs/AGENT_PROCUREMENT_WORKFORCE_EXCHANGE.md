# Multi-installation workforce exchange

This is an operator-controlled offline exchange, not live enterprise tenancy,
remote administration or verified organization identity.

## Export

On each local installation open Process catalog, then Exchange minimized
monitoring summaries across installations. Assign a unique opaque
installation alias. Generate a random 32-byte key outside OWG (for example
with a trusted password manager or `openssl rand -hex 32`) and securely
provide the same key to all participating operators. Do not put personal
identifiers in aliases. Use a different key for a different sharing group.

The dashboard-only POST
/v1/process-catalog/procurement-workforce/exchange/export accepts the
usual JSON-text envelope: document contains installation_alias,
correlation_key_hex (64 lowercase hex characters) and confirmed=true.
The key is used in memory, not persisted or returned. The packet contains
only minimized arm counts/cost bounds, opaque journal and plan tags,
case/source HMAC tags, monitoring cutoff, local inclusion and incident
flags. It omits raw case inputs, titles, workflow identifiers, financial
documents, reviewer aliases and producer credentials. Tags remain
linkable within the shared namespace. The key must be treated as sensitive;
never share the key with someone who should not correlate the packets.
The export gives a content digest and namespace fingerprint.

## Review

Send the minimized packet file via an approved transfer channel. Obtain
the packet SHA-256 separately from a trusted source operator; a digest
inside the same untrusted file cannot authenticate its origin.
The dashboard review accepts an explicit JSON bundle (maximum 8 MiB
decoded), for example:

```json
{
  "expected_correlation_namespace_sha256": "<64 hex characters>",
  "maximum_packet_age_ms": 86400000,
  "confirmed": true,
  "packets": [
    {"expected_packet_sha256": "<separately obtained digest>", "packet": {}}
  ]
}
```

Replace the example packet with the actual exported packet. The equivalent
dashboard-only endpoint is
POST /v1/process-catalog/procurement-workforce/exchange/review using the
JSON-text envelope. At most 20 installations, each with at most 20 journals
and 1,500 cases per journal. The 8 MiB request cap can impose a smaller
effective limit. Duplicate aliases, mismatched pins or namespaces, stale
or future packets, invalid metric/count/financial bounds and malformed
schemas are refused. No persistence or upload to another service occurs.

All journals sharing exact plan/case/source tags are symmetrically excluded
from totals, even if a colliding journal was already locally excluded.
Unavailability of cohort identity and future journal cutoffs also exclude
rows. Unreadable histories are counted and mark totals incomplete. Exact
matching is not proof of distinct business workloads: different identifiers
can refer to the same case, and an untrusted source can fabricate summaries.
Each currency and arm is totaled separately using exact rational bounds.
No cross-arm subtraction is presented as realized savings.

Rollback/attention flags are retained for review even if the row is excluded.
They are declarations as of export; the reviewer does not independently
recompute a source journal or execute a rollback. A review does not
authenticate an installation, prove coverage of all enterprise sites,
validate business outcomes or authorize spending or execution.
