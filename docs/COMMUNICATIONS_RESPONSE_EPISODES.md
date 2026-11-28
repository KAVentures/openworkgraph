# R22 — Communications response episodes and observed backlog

In Evidence → Communications, choose an export as usual and enable **Include
response episodes and deadline review**. Enter a timezone-qualified observation
cutoff and one to eight distinct whole-minute elapsed deadlines (1–43,200).
For CSV explicitly map timestamp, thread and direction columns. Email exports
need declared own domains. The existing local processing acknowledgement is
still mandatory; the optional event-log and redacted-example retention switches
remain independent. No extra storage is created by the response review.

## Episode accounting

Each eligible thread is ordered by UTC timestamp. The first observed inbound
after a previous observed outbound (or the thread's first observed inbound)
starts an episode. Further inbound messages before the next outbound are
counted as repeat contacts. The next outbound closes the observed episode.
Subsequent outbound messages without an intervening inbound do not open new
episodes. A last unmatched inbound remains open at the cutoff. The report
distinguishes first-observed from follow-up episodes, not first-ever contacts.

An undated message withholds the entire thread because it cannot be placed
relative to the cutoff. Fully dated messages after the cutoff are excluded.
Unknown direction before the cutoff, mixed inbound/outbound at the same
timestamp, no observed inbound and threads entirely after cutoff are separate
exclusive exclusion reasons. Same-direction ties are grouped; different
classifier labels at the first timestamp are a mixed group, never arbitrarily
assigned to one intent. Unthreaded messages are counted separately.

For each deadline, every eligible episode is classified into observed on-time,
observed late, no reply and deadline elapsed, or no reply and still within
deadline. The deadline is inclusive (integer microsecond arithmetic). The
observed share is on-time divided by all eligible episodes; the conditional
upper endpoint additionally counts pending-within-deadline episodes. Neither
is a confidence interval or a verified actual SLA. An export can omit earlier
contacts or replies. Observed reply latency and open-episode age have separate
median and nearest-rank P90 summaries.

## Backlog and operational planning

The observed backlog is open response episodes, not all outstanding work.
An exact endpoint sweep computes opening balance, inflows, observed replies,
closing balance, net change and peak open episodes. Simultaneous events
across threads are netted instead of assigned an arbitrary order.
Elapsed episode-wait exposure is the sum of open episode durations
intersecting a window; time-weighted observed backlog is exposure divided
by elapsed window duration. Zero duration has no time-weighted mean.
Waiting is not service or staff effort.

The overall window spans the earliest eligible observed episode through the
declared cutoff. Up to 26 recent UTC calendar weeks show opening balance,
new episodes, replies, closing balance, peak and exposure. Earlier episodes
that remain open contribute to carry-in. Weekly event windows are half-open
except the final inclusive cutoff; a cutoff exactly at Monday may create a
final zero-duration bucket. Groups with fewer than five distinct threads
touching the window are withheld, so displayed weeks need not be contiguous.
The overall aggregate is not calculated from the displayed subset.

Open aging bands cover 0–60, 60–240, 240–1,440, 1,440–4,320, 4,320–10,080,
10,080–43,200 and at least 43,200 minutes, lower-inclusive and upper-exclusive.
Each deadline shows how many open episodes are past or within the deadline,
due in the next hour/day relative to the cutoff, and median overdue seconds.
Due counts overlap and do not imply staffing, current live work or forecast.

## Aggregates and export

Breakdowns by first inbound intent, episode start UTC week, and first/follow-up
kind require at least five distinct threads per displayed subgroup, capped at
100 groups each. Repeated episodes from one thread do not meet that threshold.
Omitted groups are summarized by counts only. Overall and overlapping group
aggregates are not a formal anonymity or differential privacy guarantee.
No thread keys, raw message text, raw channel labels or per-thread records
are emitted by this extension. The report is ephemeral unless the operator
explicitly downloads its JSON; other existing import report fields retain
their prior behavior.

API: add both response_cutoff and response_deadlines (comma-separated
whole minutes) to the existing dashboard-only consented
POST /v1/communications/analyze route. Invalid options refuse before
temporary upload creation. Without these optional parameters the original
report shape remains unchanged. The extension supports keyword and learned
primary labels without changing their classifier or quality gates.

All new calculations concern the selected export and its declared cutoff.
No business resolution, production SLA, actual response completeness,
staffing requirement, execution approval or causal claim is verified.
No provider call, background job or additional retention is introduced.

## Verification

New tests cover reply boundaries, mixed-direction and undated exclusions,
distinct-thread suppression, cutoff handling, exact weekly balance, carry-in,
simultaneous events, empty cohorts and 160 independent randomized
minute-grid backlog and episode partition oracles. Existing authenticated
HTTP and real-browser import tests cover opt-in, deadline reports, download
and temporary upload deletion. Native integration CI remains required.
