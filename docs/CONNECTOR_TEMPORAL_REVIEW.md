# Reviewed cross-source temporal evidence

The connector temporal review is an optional owner-only analysis of a
reviewed, exact publication-pinned cross-source linkage plan. It does not
change linkage assertions, source events, checkpoints, saved plan
revisions, exports or the original review digest. Existing mining exports
remain unchanged.

## Why this matters

A business date is not a midnight event. SAP procurement evidence uses a
canonical UTC-midnight encoding of a reported business-date label; it
does not establish the clock time of an order, movement or invoice.
Generic mining of encoded timestamps can therefore mislead. The existing
linkage preview suppresses exact span/switch statistics for date-bearing
groups. This separate review offers a conservative alternative without
claiming actual occurrence times or modifying the ordinary mining engine.

## Review workflow

In Reviewed cross-source business cases, inspect exact snapshots, create
and preview the linked groups. Open **Reported-time uncertainty review**.
For each linked SAP procurement job, optionally enter an IANA timezone
such as Europe/Stockholm if a reviewed business-calendar assumption is
appropriate. Leave it empty when the alignment is unknown. Approve the
assumptions explicitly and run the review. Non-SAP source timestamps are
treated as reported points; their clock precision or accuracy is not
independently verified.

The owner endpoint POST /v1/admin/connector-events/{organization_id}/case-temporal-review
accepts the existing reviewed Plan plus expected_review_sha256,
confirm_temporal_assumptions, optional business_date_timezones keyed by
linked SAP job ID, offset, limit (1–50), and an expected temporal scope
digest for subsequent pages. No raw source identifiers, secret material
or provider reads are required. The exact publication grants and linkage
review are recomputed under the existing organization fence. Revocation,
expiry or changed pins block analysis. The complete result and timezone
assumptions are hashed into the temporal scope, so a change to the actual
derived timezone bounds also invalidates paging. Report data is computed
on demand and not retained by the Gateway beyond the existing counts-only
audit receipt. A local JSON page download is not revocable after download.

## Arithmetic and interpretation

A reported point is a degenerate closed interval [t,t]. A reviewed
business-date alignment is the closed interval from local midnight to
next local midnight in the chosen zone. Ambiguous/nonexistent midnight
boundaries and malformed date labels are rejected rather than silently
normalized. DST days can be 23 or 25 hours. The upper endpoint is included
conservatively: touching intervals are not strictly ordered. Unknown
alignment produces no interval. A date interval describes a reported
calendar label, not a guarantee that a physical action took place within
it. A posting date may be different from actual movement time.

Each cross-source event pair in a linked group belongs to exactly one
category: strictly earlier left interval, strictly earlier right interval,
equal reported points, overlapping intervals, or unaligned date pair.
Pair counts use sorted endpoints and binary searches, not quadratic
materialization. Equal timestamps do not prove simultaneous actions.
The result distinguishes unknown alignment from known overlapping
intervals. Pair counts are not handoff counts or independent observations.

For a group with all reported intervals aligned, the minimum possible
reported span is max(0, maximum lower endpoint minus minimum upper endpoint).
The maximum is the largest difference between an upper and lower endpoint
from distinct events. The single-event span is zero. Unknown alignment
suppresses the entire group's span. These extrema assume representatives
can vary independently within their reported intervals.

Only when every inter-source pair is strictly ordered in one direction
does the report provide boundary-gap bounds: between the latest earlier
source representative and earliest later source representative. This is
not process waiting time, handoff duration, physical inactivity or
causation. Missing source evidence may occupy any part of that gap.

Across linked case groups, the source-pair rollup counts groups wholly
ordered in each direction and groups not wholly separated, plus
overlapping diagnostic flags for unknown dates, overlaps, equal reported
points and both strict ordering directions. Conditional directional p50
and p90 gap bounds use nearest-rank empirical quantiles of lower and upper
bounds, weighting each wholly separated linked group once. Excluded
groups are not assigned a zero gap. These conditional quantiles should
not be compared without reviewing coverage and case mix. Pair counts are
reported separately because large cases can dominate them.

Only linked groups are included. Unlinked cases remain in the original
linkage export according to its reviewed plan, not in this report.
The 20,000-event combined publication bound, 500-group bound, 2,000-member
bound, 10-source bound and 50-group page limit apply. Output is conditional
on the selected reported evidence and operator assumptions, not complete
source history or independently verified business identity.

## Validation

The exact pure interval module was locally verified by matching its Git
blob hash and running 5,000 randomized pair-count comparisons against a
quadratic oracle, 2,000 exhaustive discrete span comparisons, DST and
skipped-date checks, and a 100-million-pair counting fixture. Storage,
owner authentication, pin/revocation, conditional rollup and browser
regressions are registered in CI. Full repository checkout was blocked
by DNS in the authoring environment, and those integrated tests have
not yet executed locally. This follow-up does not replace the required
consolidated enterprise CI and real provider acceptance.
