# R15 — Implementation history and forecast controls

Open a saved implementation plan in the shared process portal, then expand
Implementation forecast history and calendar controls. Load exact history,
select two ordered revisions and a UTC review date (not in the future), and
compare. The complete bounded history (up to 50 revisions) is read under the
existing tenant-scoped catalog transaction. All stored revision digests are
validated; gaps, corrupt snapshots and changed access/history pins refuse
rather than silently compare partial data.

## Forecast history

The timeline shows exact baseline cost and effort, reported actuals,
remaining estimates, and forecast (actual plus remaining). For every quantity,
complete totals require complete phase coverage. A separate lower bound sums
known nonnegative amounts, even when only one forecast component is known.
This is a bound on the declared nonnegative amounts, not verified spending.
Unknown amounts are not zero and headroom is not established by a low partial
sum. A known forecast lower bound above the current compatible opportunity
budget establishes a minimum excess on that declared basis. Budget context
is shown only when the later revision is the current revision, the currency
is compatible and the current source budget is available. Current source
approval and blockers are disclosed; no spending is authorized.

## Revision comparison

Compare exact phase IDs across snapshots. Retained phase changes, added scope
and signed removed scope are accounted separately for baseline, actual,
remaining and forecast amounts. An absent phase contributes zero to a scope
bridge; an existing phase with unknown amount does not. If any relevant
amount is unknown, the complete change is withheld and known partial
components are labeled as such. Negative reported actual changes represent
human revisions or corrections, not verified refunds. Currency changes
prevent monetary bridges across different currencies; a multi-currency history
also withholds current budget comparisons because report amounts have no
separate currency field. No conversion is performed. Effort uses hours.

Review exact baseline and report fields changed, phase membership, planned
date shifts, previous/current report attribution, opportunity-reference
changes, baseline reassessment (even with identical content) and stored
independent-review changes. A stored historical review is not an assertion
of present approval. All underlying records remain unchanged.

## Calendar review

Compare planned and reported dates in the selected later snapshot against
an explicit review date. A passed planned start with no reported start or a
passed planned finish with no terminal report is a review prompt, not proof
that work has not occurred. Completed/cancelled reports are distinct; a
reported finish later than plan is visible. A snapshot saved after the
review date, a report recorded afterward, or an actual date after that date
is flagged and excluded from lateness findings. This is not a reconstruction
of what was known at a past date, a critical path or a forecast finish.
Days are Gregorian calendar days; the planned date itself is not overdue.

## API and security

Human process read access, existing tenant isolation and no-store responses:

    GET /v1/human/processes/{workflow_id}/implementation-plan/history
    POST /v1/human/processes/{workflow_id}/implementation-plan/history/compare

Compare request: before_revision, after_revision, expected_history_sha256
and as_of_date (YYYY-MM-DD). The history pin binds root, access revision
and all exact saved snapshot references. The comparison digest also binds
review settings and the current source context where applicable. These are
not signatures. Reads are audited with identifiers and counts only. The
review creates no new retained store, changes no plan, and grants no
approval, execution, spending or benefit claims. Explicit JSON export is
a point-in-time review artifact. Dirty same-page edits and known opportunity
changes invalidate or disable review controls; reload after remote changes.

## Verification

New tests cover 200 randomized independent integer-cent scope bridges and partial forecast bounds, actual saved history, rebaseline review invalidation, stale pins, calendar date exclusions, corrupted history, strict inputs, authenticated HTTP and real browser review/download. Native CI remains required.
