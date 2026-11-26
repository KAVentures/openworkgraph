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

## Signed exchange (recommended)

The dashboard defaults to signed export/review. Use Show installation public
signing key to create/read a stable Ed25519 public key. The private seed is
stored in the existing local authentication directory using its existing
secret-file permissions; it is not included in the exported packet, public
identity or unencrypted credential-excluding backups. Explicit encrypted
credential backups may preserve the seed. A cloned or restored credential
store can reproduce the signing identity; do not treat it as a hardware-bound
installation identifier. Deleting the seed externally will require re-enrollment
of the new key; no automatic rotation or revocation is implied.

POST exchange/identity accepts {confirmed:true}; exchange/signed-export
accepts the same document as the original export. The latter returns a
signed_packet (expected_packet_sha256, packet, signature_hex). Signatures
use Ed25519 over the fixed ASCII protocol domain plus a NUL and the raw
32-byte canonical packet SHA-256 digest. The packet commits to alias,
namespace, timestamp, and all summary fields. The public key is not a
self-authenticating claim; obtain it and verify its SHA-256 fingerprint
through a separately trusted administrative channel before review.

POST exchange/signed-review accepts the original review document with
signed packet objects and an additional trust list. Each trust entry has
installation_alias, public_key_hex (32-byte raw Ed25519 public key, lowercase
hex), and correlation_namespace_sha256. The list must match the exact set
of packet aliases; aliases and public keys cannot be reused across entries.
Every signature must verify against that list before aggregation. No fallback
to unsigned review occurs on failure. The original freshness, integrity,
overlap and conservative accounting checks still apply.

The report distinguishes key-signature verification from organizational
identity and outcome verification. This is offline, nonpersistent review:
no global trust registry, credential revocation propagation, authenticated
live transport or persistent anti-replay history is provided. A still-fresh
signed packet can be reviewed again; it is not counted twice within a single
review. Trust only keys whose operator ownership you have established.

## Retained local trust registry

The default signed review mode can use a retained local registry rather than
supplying trust keys on every review. Load local trust registry in the
exchange dashboard. Enroll a separately verified public key for an exact
alias and correlation namespace; rotation retires the previous key and
preserves the latest accepted packet watermark. Revocation disables the
alias and never implicitly re-enables it. Revoked aliases can only be
reactivated by explicit rotation to a new key. Retired keys cannot be
reused within their namespace. This local registry is not an organization
identity provider or remote revocation service.

The dashboard-only exchange/trust-status accepts document {}.
exchange/trust-configure accepts expected_revision, installation_alias,
correlation_namespace_sha256, action (enroll, rotate, revoke), public_key_hex
(null for revoke), confirmed=true. Mutations require the latest registry
revision; the registry is bounded to 100 aliases, 20 active per namespace
and 1,000 retired keys. No silent pruning or destructive reset is provided.

exchange/registered-review accepts expected_revision instead of trust,
expected_correlation_namespace_sha256, maximum_packet_age_ms, signed packets
and confirmed=true. It requires exactly one fresh signed packet from every
active registered alias in the namespace. Each timestamp must advance the
retained watermark or exactly repeat the same content digest at that timestamp.
Older/conflicting snapshots are rejected, including after key rotation. A
successful batch atomically advances all new pins; exact repeat reviews do
not mutate the registry. Only public keys, aliases, namespace and latest
accepted timestamps/digests are retained, not packet contents. A restored
or administrator-modified database can bypass these local watermarks.
No external synchronization or proof of signer identity is established.
