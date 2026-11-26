# Shared analysis portfolio comparison (R4/M2)

In the company Shared analyses portal, open an authorized publication and exact revision and choose **Add this exact revision to portfolio**. Repeat for two to eight distinct publications, choose a baseline, and explicitly compare. Revising a selection replaces its previous pin; selections remain in browser memory only. Clearing or disconnecting removes the review. This does not change the existing same-publication revision comparison.

The POST /v1/human/analysis-portfolio/compare endpoint accepts selections (view_id, revision, expected_content_sha256) and baseline_index. It uses the existing analysis principal and per-publication ACL/expiry/integrity checks, acquires locks in canonical order in one transaction and refuses the entire request if any selection is inaccessible, expired, invalid or has a mismatched digest. No case attachments or case grants are queried. Audit is committed in the same transaction and includes exact revision IDs, digests and comparison counts, not titles or measure values. JSON export repeats the authenticated comparison instead of exporting an earlier cached result.

The existing comparison engine aligns only complete ungrouped numeric snapshots with matching analysis specifications, units, definitions and coverage. Group codes have no shared identity. Individual incompatible pairs have explicit refusal reasons; no deltas are invented. There is no pooling, rank, causal effect, savings estimate, source verification or execution permission. Exact previously selected revisions do not change when a newer revision is published. Source populations may overlap; source hashes are publisher claims. No extra access is granted. Limits: eight selections, ten retained revisions per view, 8 KiB request, existing snapshot bounds. The output is authorized local browser disclosure, not anonymization.

Tests exercise explicit baseline selection, exact numeric changes, incompatible group codes, ACL revocation, foreign tenants, stale digest, corruption, exact old revisions and real HTTP bounds. Browser and native database matrix acceptance remain required before merge.

Portfolio review additionally detects contradictory complete ungrouped numeric snapshots anywhere in the selected set when their claimed source, specification, metric version, fingerprint algorithm and columns agree. Comparisons involving either conflicting member are withheld, even if the baseline has a different source. Conflicts are displayed as exact selected revision pairs and audit records only the count. This does not establish which source is correct.

## Optional all-pairs compatibility review

Select **Include all-pairs compatibility and exact comparisons** before review.
The same endpoint accepts the strict optional boolean `include_pairwise`
(default false), preserving the existing baseline response for older clients.
After every selected revision passes the same transactional ACL, expiry,
integrity and digest checks, every unordered pair is compared independently
using the existing engine. At most 28 pairs are returned. Each pair is oriented
from the lower selection index to the higher one; differences are right minus
left. Contradictory claimed-basis members are withheld from every involved
pair, regardless of the baseline. The optional report includes per-selection
neighbor coverage, counts of refusal reasons (which can overlap), and maximal
sets whose members are all directly comparable. These sets can overlap; they
are not partitions, pooled cohorts, rankings or evidence of population
independence. No transitive comparison is inferred. A selected pair can be
inspected in the portal and the optional report is included in a fresh,
access-rechecked JSON download. The report digest covers the entire result;
audit adds only pair counts, not values. Selection changes or disconnect clear
the current report. The bounded maximal-set search examines at most 255
subsets. No additional case access, source verification or execution permission.
