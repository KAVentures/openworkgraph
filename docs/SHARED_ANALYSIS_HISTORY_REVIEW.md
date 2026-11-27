# Shared analysis revision-history and target workbench (R4)

Use the company Shared analyses portal, open a publication with at least two
revisions, then expand **Review complete revision history and targets**.
Load retained revision pins, choose the last revision of a contiguous prefix
and the exact anchor revision. Optional targets are declared independently
for each numeric anchor measure: at least, at most, or inclusive range.

The review reports adjacent changes and anchor-relative changes, the exact
publication order, selected population counts, all conflicting claimed-basis
pairs, target status and distance outside target, consecutive met revisions,
and every adjacent target transition. Publication order is not a time series.
A withheld revision is neither success nor failure and breaks the streak;
there is no interpolation or implied distinct population.

## API

- GET /v1/human/analyses/{view_id}/history-review returns a revision inventory
  (no result rows), exact digests, measure definitions and the latest revision.
- POST to the same path accepts a contiguous list of two to ten exact pins,
  an anchor revision within that prefix and up to six optional targets.
  Each pin includes revision and expected_content_sha256. Each target has
  heading, operator (at_least, at_most or between), threshold and, for
  between only, upper. Numeric thresholds are fixed-point strings with
  bounded length (31 integer and 30 fractional digits); no exponents or NaN.
  Comparisons are inclusive. Exact target distances are calculated using
  rational arithmetic over published decimal representations.

Every operation uses the existing human principal, transaction, per-view ACL,
expiry and stored-content verification. All pinned revisions must resolve
before any report is returned. Newer publications are counted but never
silently included. Case attachments and case grants are never read.
Audit entries include revision identifiers, digests and counts, not values,
thresholds, titles or case codes. Responses are no-store. There is no separate
persistent review store and no change to publication or access policy.

The existing pairwise comparison engine rejects grouped, truncated,
incompatible and incomplete numeric reports. Contradictions across every
pair in the reviewed prefix suppress all comparisons involving the affected
revisions. A target result is withheld unless its revision is directly
compatible with the exact anchor. An adjacent transition also requires a
directly compatible adjacent pair. The report digest covers the entire
result; because the current latest revision is included, it changes when
the latest retained revision changes even for an unchanged historical prefix.

The browser clears history and inputs on publication or identity changes;
changing any target, anchor or prefix invalidates previous results. Download
makes a new authenticated review request, including fresh ACL and digest
checks. A downloaded report cannot be recalled after delivery.

These are descriptive targets declared by the reviewer, not source verification,
adopted policy, causal effects, savings, employee ranking or execution authority.
No group identities are joined, totals pooled or historical authority propagated.

## Regression coverage

Run python -m pytest -q tests/test_shared_analysis_history_review.py.
The existing gateway database CI matrix includes the new test file. The
shared-analyses browser test additionally opens a real publication history,
reviews target crossings, downloads a fresh report, changes the prefix
and verifies stale result clearing. Native CI and actual browser execution
are required before release.
