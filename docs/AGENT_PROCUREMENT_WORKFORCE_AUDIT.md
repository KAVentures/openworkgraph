# Portable workforce procurement audit dossiers (R26)

Customers and their auditors should be able to reproduce the same evaluation
without sharing local databases, agent credentials or purchasing a particular
automation vendor's reporting tool. A dossier captures the currently selected
complete signed packet cohort and the exactly recomputed signed review. It is
not an independently attested financial statement or a deployment approval.

## Export and transfer

Open Process catalog → retained signed installation inbox. Load status for the
intended namespace and maximum age, then select **Export independently
verifiable signed audit dossier**. Every active enrolled installation must
have a fresh, signature-verified packet. The server checks both the registry
and inbox revision in one local transaction, rereads packet contents, and
recomputes the review. The export does not advance accepted review watermarks.
The existing registered review remains a separate explicit operation.

Confirm export of linkable opaque case/source tags and transfer the downloaded
JSON through an approved channel. Communicate its displayed dossier digest
through a separately trusted channel. The dossier digest is SHA-256 over
canonical JSON of the dossier excluding dossier_sha256, not the bytewise hash
of the downloadable file. Obtain each installation's public key and alias
mapping independently; do not treat keys shipped by an untrusted dossier
sender as trusted. No public-key trust list, signing seeds, correlation keys,
raw case inputs, or producer credentials are included in the dossier.

This dossier does not include sticky historical inbox incident rows (which
may reference packets no longer retained). Export the inbox status separately
when that evidence is needed. A clean current dossier does not clear a
previously recorded rollback-review referral.

API: dashboard-session-only POST
/v1/process-catalog/procurement-workforce/exchange/inbox-dossier, standard
JSON-text document envelope. The document has the same fields as inbox-review
(expected_revision, expected_inbox_revision, correlation_namespace_sha256,
maximum_packet_age_ms, confirmed=true). The response is no-store. There is
no provider call, remote endpoint or external action.

## Independent local verification

Use the installed OWG Python environment and code version implementing
this dossier format:

```sh
python -m server.agent_procurement_workforce_dossier verify dossier.json trust.json --sha256 <separately-obtained-digest> receipt.json
```

The trust file has this shape (replace placeholder values):

```json
{
  "expected_correlation_namespace_sha256": "<64 lowercase hex characters>",
  "trust": [
    {
      "installation_alias": "site-a",
      "public_key_hex": "<32-byte Ed25519 public key, lowercase hex>",
      "correlation_namespace_sha256": "<same namespace digest>"
    }
  ]
}
```

Include exactly the trusted aliases for this archive. The verifier checks the
independently provided dossier digest, canonical packet representation, exact
namespace and trust set, signatures, timestamp freshness relative to the
recorded review clock, overlap exclusion, exact rational accounting and the
entire recomputed report. It refuses altered results even if they have been
rehashed. The verifier does not establish a trusted timestamp, freshness at
verification time, organization identity, current key revocation, source
truth, or whether the installations represent the entire organization.

For customers who prefer no local dashboard dependency for review, create a
dossier directly from the existing signed-review request format (including
separately pinned public keys and confirmed=true):

```sh
python -m server.agent_procurement_workforce_dossier freeze signed-review-request.json dossier.json --confirm-linkable-export
```

## Compare historical snapshots

```sh
python -m server.agent_procurement_workforce_dossier compare earlier.json later.json trust.json --previous-sha256 <earlier-digest> --current-sha256 <later-digest> delta.json
```

Both dossiers are independently recomputed. Comparison requires the same
namespace, exact trusted alias/key mapping, same freshness policy, advancing
review clock, and no older or conflicting packet for an installation. For
each opaque journal, inspect present/absent/changed cohort status, exclusion
reasons, recorded counts and rollback-review flags from both snapshots.
The comparison retains a prior flag even when the journal disappears. It does
not sum repeated snapshots, infer resolution or compute financial improvement
from different cohort populations. Key rotation or changed participating
installations need separately verified dossiers and human review.

All commands bound file sizes to 40 MiB, reject duplicate JSON keys and
non-finite constants, and refuse to overwrite any existing output. New
CLI files are created with mode 0600 on POSIX; the dashboard download uses
browser-controlled storage permissions. A failed write may leave a partial
file; never treat it as a valid dossier. Do not send linkable artifacts to
unapproved recipients. Exact historical recomputation can require the same
OWG report implementation; no indefinite cross-version compatibility or
trusted archival storage is claimed.
