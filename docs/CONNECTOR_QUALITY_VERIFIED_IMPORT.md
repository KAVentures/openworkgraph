# Verified quality-annotated enterprise evidence import

Enterprise delivery #1572. Follow-up to #1613 and #1610, complementary to
the Gateway source-quality console in #1602. Quality reports describe
operator-selected source samples, not verified business compliance or the
current contents of a mutable local mining log.

## Ordinary quality-reviewed bundles

In the Gateway publication-quality console (#1602), review exact source
publications and export the passing quality-reviewed JSON. The existing
ordinary event bundle now carries a quality_review object.

In the local **Import published connector evidence** page, select this
file and approve inspection. The importer validates every source manifest
and original row, reconstructs the complete source-specific quality
report using the declared policies and exact selected source rows, and
compares its canonical digest and all report fields. It does not merely
check a supplied passing flag. A wrong metric, different grant/manifest,
malformed policy, unknown field, failed gate or indeterminate gate causes
rejection. Recalculating the hash of incorrect diagnostics cannot make
the file pass. An attacker able to change both the evidence and its
consistent report can still produce a self-consistent package; there is
no cryptographic signature.

Review the recalculated source gates and their exact digest before
separately approving origin, quality and local retention. The local
receipt retains the counts-only recalculated report, policy fingerprint
and canonical package fingerprint alongside the original source manifests.
The raw-file digest binds exact submitted bytes.

## Portable quality-reviewed linkage

Download a passing counts-only report from the Gateway quality console for
the exact same publications as the reviewed linked case plan. In the
portable export section of the case review page, attach the report file
and approve the complete original evidence disclosure and the quality
policy separately. An invalid attachment cannot silently downgrade the
export to an ordinary portable package. Explicitly clearing the attachment
returns to the independently authorized ordinary export path.

The Gateway recomputes the original source report under the same locked
organization transaction as the portable linked-case export. Organization,
grant IDs, manifest digests, publication revisions and declared expiries
must match the live reviewed scope exactly. Both review and source-quality
digests are preserved in the resulting portable package. Its complete
original source rows are still disclosed, including excluded unlinked
cases. The local importer verifies the original source report and the
deterministic linked projection, and requires both linkage and quality
approval. Only the derived projection is retained in the local mining log;
quality metrics describe the complete original source sample, not the
filtered linked view.

Legacy derived linked JSON without original source rows remains unsupported.
The new format is the portable linkage envelope from #1613 with one
additional quality_review object. Ordinary and plain portable imports
continue to work with their existing consent paths.

## Append and provenance semantics

The quality-policy fingerprint binds the declared organization, default
policy and replacement policy of each exact source job. Ordinary incremental
append requires the same fingerprint and the existing exact source-profile
scope. A different quality policy, adding quality provenance to a plain
log, or removing quality provenance requires a new log. A different
portable linkage package still requires a new log. Exact replay returns
the existing receipt.

Each committed receipt describes the particular imported sample. Even if
each imported window passes the same policy, the union of windows may not
pass that policy. Generic workspace mutation does not certify the log and
is outside this verified append workflow. No global organization-wide
mandatory gate or current-log quality certification is introduced.

The report's organization ID, publication revisions and expiries are
declared offline metadata. For ordinary quality-annotated bundles the
original event bundle does not independently supply those fields. They
are validated structurally and incorporated into the digest, but are
not authenticated. For portable linkage they must additionally match the
envelope pins. Offline import never verifies current grant status, physical
occurrence times, actual business identity or provider completeness.
Revocation does not erase previously downloaded files.

## API and implementation

The local preview endpoint accepts quality_review on either supported
bundle format. Retention adds X-OWG-Bundle-Quality-Reviewed: true as a
separate required header for annotated imports. The portable Gateway
export endpoint accepts optional quality_review_json (the report's original
UTF-8 JSON text, maximum 128 KiB) and explicit
confirm_quality_gated_export. It rejects duplicate JSON keys, oversized
reports and mismatched quality scope. The existing total 16 MiB portable
package limit applies after adding the quality report.

The pure diagnostics module is the exact implementation from #1602.
The new shared quality verifier reconstructs that report contract without
a Gateway or database dependency. The frozen v1 Gateway report assembly
is used as a regression reference; reconcile the shared module and
overlapping case-review UI/route changes when landing #1602 with this
stack. The Gateway quality console itself remains in #1602.

## Validation

Five exact Git blob hashes checked against the locally reproduced source:

- connector_quality_verification.py: 9d09918fc8b48f5ccd579d92699cfd6e63391a4e
- connector_publication_quality.py: f6d3d04ce2e0b600a69736d82cc07fca0111eec9
- connector_bundle_import.py: 16d84e184b7936af5c5a0525a08486dceb7caaa0
- connector_event_snapshot.py: 563f1af657d194e2d236d6b7352f098e9f47b847
- enterprise_connector_contract.py: 0f5aaad4349a35a173793b9e1bcbb0b7c23dc5f2

Local isolated checks passed 300 generated validated bundles with
independent count/rate assertions, 300 input-order permutations, and
900 altered-report rejections after recomputing the attacker-supplied
digest. SAP date-only indeterminate gates, failed thresholds and explicit
source overrides passed their expected outcome checks. Quality-only
recomputation of an already validated 20,000-event bundle took 0.066 s
in the authoring environment; source decoding, report creation and
recheck together took 1.604 s. These are isolated synthetic timings,
not end-to-end import or Gateway throughput.

Added a reproducible pure pytest fixture with frozen #1602 report parity,
actual SQLite/PostgreSQL publication and local DuckDB retention fixtures,
and Chromium quality-report attachment/export and dual-approval import
acceptance. The complete pytest and browser workflows remain pending CI.
Direct GitHub checkout is blocked by DNS and DuckDB is not available in
the authoring environment. Land after parent integration acceptance
and reconcile #1602's overlapping changes before release.
