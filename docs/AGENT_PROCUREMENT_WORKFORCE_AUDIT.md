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


## Multi-snapshot independent audit timeline

For an audit involving 2–12 historical dossiers, including installations that
have temporarily disappeared or changed signing keys, obtain a separately
trusted manifest of the ordered dossier digests and historical trust sets.
Use this format:

```json
{
  "format": "openworkgraph.workforce-audit-timeline-manifest.v1",
  "entries": [
    {
      "dossier_sha256": "<first independently obtained digest>",
      "trust": {
        "expected_correlation_namespace_sha256": "<namespace>",
        "trust": [
          {
            "installation_alias": "site-a",
            "public_key_hex": "<historical public key>",
            "correlation_namespace_sha256": "<namespace>"
          }
        ]
      }
    },
    {
      "dossier_sha256": "<second independently obtained digest>",
      "trust": {
        "expected_correlation_namespace_sha256": "<namespace>",
        "trust": [
          {
            "installation_alias": "site-a",
            "public_key_hex": "<later historical public key>",
            "correlation_namespace_sha256": "<namespace>"
          }
        ]
      }
    }
  ]
}
```

Obtain the manifest's canonical JSON SHA-256 separately from the authorized
audit coordinator. The manifest must not be trusted merely because it was
supplied alongside the dossiers. Then run:

```sh
python -m server.agent_procurement_workforce_timeline manifest.json --manifest-sha256 <separately-obtained-digest> --output timeline.json first.json second.json
```

Files must appear in manifest order. The timeline recomputes every signed
report, checks strict review-clock progression and a single namespace,
rejects duplicate dossiers, backwards or conflicting per-installation
packets (including after a site disappears and reappears), key reassignment
to a different alias, case-only alias changes, and reuse of a previously
retired key. A separately pinned new key can be recorded as a historical
key change; the tool does not verify who authorized that change. Changes in
the declared freshness policy and participating installation set are
displayed, not silently normalized.

The resulting minimized timeline shows the first/last snapshot and
observation count for every seen journal; changed or unavailable case
cohorts; exclusions; cutoff regressions against the historical high-water
mark; and every supplied snapshot in which a rollback-review signal was
observed. It never interprets later absence or a clean snapshot as clearance.
The timeline does not contain case/source correlation tags or raw packets;
journal tags and installation aliases remain linkable. No across-snapshot
financial sums or causal comparisons are calculated.

The manifest and dossiers must come from trusted custody. Strictly advancing
declared times do not prove trusted chronology; a missing snapshot may
contain an unobserved incident. The report does not verify revocation,
organization identity, completeness of historical custody or live rollout
authority. Individual input files and the output are capped at 40 MiB;
aggregate dossier input is capped at 160 MiB. Output is exclusive/private
under the same conditions as the original audit CLI.


## Local dashboard verification workbench

The receiving auditor does not need to enroll keys in the local trust registry,
retain packets or use the command line for smaller archives.

1. Open **Process catalog → Exchange minimized monitoring summaries across
   installations → Independently verify historical workforce audit evidence**.
2. Select a single dossier or ordered timeline. Supply the separately obtained
   expected SHA-256 digest (dossier digest for a single review, manifest digest
   for a timeline), and the corresponding independent trust JSON or manifest.
3. Select each dossier individually in exact manifest order. Confirm the
   local recomputation. No requests are initiated solely by selecting files.
4. Review the independently recomputed historical report, or inspect the
   timeline snapshot summaries and filter/search/page through the complete
   minimized journal queue. Download the minimized review for your records.
   Changing an input clears the prior result and ignores a pending response.

The browser enforces an 8 MiB combined original-text request limit, including
the JSON wrapper; larger requests must use the offline CLI. Invalid UTF-8,
non-object files, malformed/duplicate JSON, wrong pins, altered reports and
out-of-order timelines are refused. Browser syntax checks do not replace the
server's strict parser or cryptographic verification. Numeric text is not
re-serialized by the browser before verification.

The workbench does not retain dossiers, trust keys, manifest contents or
reports in the local registry/database, and requires a dashboard session and
explicit operator confirmation (AI-context and bearer-token requests are
refused). The response contains only a minimized recomputed report/receipt
or timeline, not the underlying signed packets. File selection and processing
take place on the local installation. Browser downloads and any OS/browser
caches remain the user's responsibility.

API actions: POST
/v1/process-catalog/procurement-workforce/exchange/audit-verify
with the standard JSON-text document envelope containing
`confirmed=true`, `expected_dossier_sha256`, `trust` and `dossier`;
POST /v1/process-catalog/procurement-workforce/exchange/audit-timeline
with `confirmed=true`, `expected_manifest_sha256`, `manifest` and
the ordered `dossiers` array. The server requires the exact keys and
strict confirmation, recomputes all reports, returns no-store responses,
and does not consult the retained trust registry. Neither endpoint confers
current trust, verified chronology, incident clearance or execution authority.
