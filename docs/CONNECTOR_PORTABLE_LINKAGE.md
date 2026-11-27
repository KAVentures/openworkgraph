# Portable reviewed linked evidence

Enterprise roadmap #1572. Follow-up to #1610 and #1589.

The ordinary Gateway linked-case export contains transformed event rows,
a reviewed group report and source manifests. Those transformed rows no
longer have their original source case IDs or activity labels and cannot
reconstruct the original source manifest digests by themselves. This
follow-up adds a separate complete-source portable evidence format that
can be verified and reconstructed directly in the local mining workspace.

## Operator workflow

1. Open Gateway **Reviewed cross-source business cases**, select the exact
   approved publications, review case memberships and preview the linkage.
2. Approve the existing linkage assertions and projected event disclosure.
   Separately approve **complete original source disclosure**. The portable
   file contains every original published event, including source cases
   excluded by the linked-view filter. Do not select this export if you
   are authorized to disclose only the derived event view.
3. Download **Export portable linked evidence JSON**. The server validates
   original live grants, recomputes the exact linked review and projection,
   and verifies the portable package before returning it. No provider
   read, credential disclosure or automatic linking is involved.
4. In the local dashboard, open Enterprise connector operations → Import
   published connector evidence. Choose the file, approve inspection and
   review the exact file and canonical package digests, source manifest
   summaries, input and retained case/event counts, excluded row count,
   linkage review and projection digests.
5. Separately approve origin review, retention and acceptance of the
   supplied linkage assertions. Import into a new mining log. The local
   server reconstructs and checks the same reviewed projection; only the
   derived selected events are retained in the log. The original source
   rows excluded by the plan are not imported.
6. Review receipt history and analyze the linked case notion in the normal
   local mining dashboard. Re-importing the same exact package into that
   target returns its existing receipt without appending events. A changed
   linkage or publication scope requires a new log, even if its derived
   row set appears unchanged. The ordinary generic append workflow still
   exists; it is not a verified continuation of this linked package.

## Portable format and trust boundary

The JSON format openworkgraph.portable-reviewed-connector-cases.v1 contains
the complete ordinary source bundle, exact publication pins and declared
expiry, organization identifier, reviewed groups and evidence basis,
many-to-one confirmation, unlinked inclusion choice, and scope, review
and projected-row SHA-256 digests.

The strict verifier first reconstructs every ordinary source manifest
from the original canonical rows. It checks all pins and disjoint group
membership, including explicit same-source many-to-one approval, then
recomputes the exact reviewed case projection and review digest using the
shared Gateway/local deterministic calculation. It preserves original
source-event IDs and source namespaces; linked members share the
review-specific case namespace reviewed_<review digest prefix>. Source
activity labels are prefixed with the source job ID, as in the existing
Gateway export. Date-only evidence and timestamp ties retain the existing
review semantics. Source-order permutations normalize to the same canonical
package digest; the raw-file digest still binds exact bytes.

A cryptographic digest is not a signature. A party able to change the
package can also recompute its unkeyed hashes. The local verifier does not
authenticate the Gateway, perform a live grant lookup, prove that a
declared expiry is genuine, establish provider completeness or verify
that operator-linked members represent the same business record. A valid
offline package may have been downloaded before the source grant was
revoked. Local copies and files are not recalled by Gateway expiry.

The portable file contains original pseudonymous source case/event IDs
and approved activity codes or labels, as well as operator-declared
memberships. Handle it as sensitive enterprise evidence. The receipt
contains the source manifest summary, linkage/projection digests, counts
and explicit offline trust limitations; it does not retain the excluded
original event rows or full membership lists. The original downloaded
file remains under the operator's control. No raw provider record payload,
source credential or native case ID is introduced by this workflow.

## Bounds and API

Same 16 MiB UTF-8 upload budget, 2–10 sources, 5,000 events/source,
20,000 total events, 500 linked groups and 2,000 group members, at most
20 members per group. The Gateway rejects over-budget packages.
Owner-only POST /v1/admin/connector-events/{organization_id}/case-export-portable
accepts the ordinary reviewed export request plus
confirm_complete_source_disclosure. Local POST
/v1/enterprise-connectors/bundle-import/retain accepts the existing
headers and requires X-OWG-Bundle-Linkage-Reviewed: true for this format.
The existing ordinary JSON and machine CSV Gateway exports are unchanged.
Legacy derived linked JSON and quality-annotated bundles remain rejected
by the direct importer.

The local atomic retention transaction verifies each projected source key,
activity, timestamp and reviewed case membership before committing the
receipt. Log deletion removes the receipt. Ordinary source-bundle
incremental imports continue to use their existing source-scope checks;
portable reviewed linkage permits only exact replay into its original
target, not incremental merging of different review namespaces.

## Validation

The exact five pure verifier/dependency files were reproduced locally
and their Git blob hashes checked against the branch:

- connector_bundle_import.py: a713ed4dc5e884f36995bd2ff3908e268a5d08a4
- connector_case_projection.py: 5db12d3d09b359b21c7062853b5229315886e516
- connector_portable_linkage.py: f5fd32df2b296c5379af3bd2a493ccc0ce6140bb
- connector_event_snapshot.py: 563f1af657d194e2d236d6b7352f098e9f47b847
- enterprise_connector_contract.py: 0f5aaad4349a35a173793b9e1bcbb0b7c23dc5f2

Local generated fixtures passed 300 projection/count checks, 300 shuffled
package checks and 300 altered linkage rejections. A full 20,000-event
synthetic package (7,068,002 bytes) parsed and verified in 0.922 seconds.
This is algorithm-only and not a database or Gateway throughput benchmark.
The local generated package uses the shared computation for its expected
digest; independent count assertions and tampering checks supplement it.

Actual Gateway SQLite/PostgreSQL and retained DuckDB regression fixtures
cover parity with the existing live export, excluded-row handling, exact
retention/replay, changed linkage, tampering, permissions and revocation.
Chromium fixtures exercise the separate complete-source export consent and
the local portable-file import consent. Focused draft acceptance is
registered. Full local checkout failed DNS and DuckDB is not installed;
integrated tests remain pending. Land after #1610 and #1589 acceptance,
reconciling overlapping Gateway case review and UI follow-ups.
