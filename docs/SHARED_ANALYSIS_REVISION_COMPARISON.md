# R4 — Compare retained, authorized company analysis revisions

OWG retains up to ten immutable revisions of an explicitly shared numeric
analysis. This adds a read-only **Compare retained revisions** panel to
the existing `/analyses` company portal and a bounded JSON endpoint:

```http
GET /v1/human/analyses/{view_id}/compare?left=1&right=2
```

The caller signs in using the **existing company analysis session or delegated
OIDC token** with `org:analysis:read` and current membership of this exact
publication, or the existing explicit analysis-admin scope. The underlying
published rows are digest-verified in one tenant transaction. Comparisons are
audited with revision numbers, digests and refusal reasons; audit records do
not include numeric cells or group labels. Revocation/retention expiration
takes effect on the next request, exactly as for existing publication reads.

## Which numeric comparisons are meaningful?

Both retained revisions must supply all of the following:

- The same case/event analysis level, `analysis_spec_sha256`, metric-definition
  digests, column definitions/aggregation methods/units and fingerprint algorithm.
- **No group columns.** `G1_1` in one export need not identify the same underlying
  group as `G1_1` in the next. No group-row remapping, person matching or
  inferred case identity is attempted.
- **One full numeric row each**, no row truncation, non-finite suppression,
  null numeric cells, or incomplete custom metric coverage. An unversioned
  derived case metric is refused rather than assumed identical.
- If the source digest is identical in both files, the published numeric cells
  and case/event population counts must also agree. Conflicting publisher
  claims fail closed instead of producing a plausible-looking trend.

Only then does OWG show *previous*, *current*, an exact base-ten
`change_decimal` and a descriptive relative percentage (rounded to two
decimal places). Relative change is undefined for a zero baseline; extremely
large ratios are omitted rather than printed imprecisely. The source digest
may differ between comparable revisions: a revisioned source can contain
additional or changed observations while the declared analysis specification
remains the same.

The payload uses `openworkgraph.analysis-revision-comparison.v1` and returns
`comparable=false` with explicit `refusal_reasons` and **no** `changes`
when these checks fail. Even refused comparisons include only the two
authorized revision IDs, published titles, digests, timestamps, complete
population counts and status. The user can open each original revision
separately within the same existing authorization.

**Important**: The Gateway does not independently possess or verify the
underlying local event log. Digests and numeric values are publisher-supplied
claims; the store verifies that the immutable publication bytes have not
changed, not that the original source data were correct or complete. Numerical
differences are descriptive, not proof of causality, savings, reduced employee
effort or a production-grade longitudinal cohort. A revised source may also
change the membership of a cohort. Full-source freshness and independent
business outcome verification remain separate roadmap gates.

## Acceptance and exclusions

- SQLite and PostgreSQL tests for exact numeric differences, zero-baseline
  suppression, incompatible specifications, metric coverage, group-code
  refusal, same-source conflicting values, current membership, tenant denial,
  expired/revoked access, unauthorized identities and corrupt revision refusal.
- Production shared-analyses Chromium acceptance checks the refusal UI for
  two independently coded aggregate snapshots; it never silently joins group
  codes or displays a misleading delta.
- No new database, permissions, data upload, third-party dependency, automatic
  notification, execution authority or broader export is introduced.
