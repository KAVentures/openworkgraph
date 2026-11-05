# Observed case-duration distribution

The **Case duration distribution** in the local event-log workspace shows how
many selected cases fall into each elapsed-time band. This makes the long tail
visible alongside the existing median and 90th-percentile KPIs. Choose 1–10
strictly increasing whole-number thresholds and seconds, minutes, hours or days;
for example, `1, 4, 24` hours shows four bands: under one hour; one to under four;
four to under 24; and 24 hours or more. Each threshold is inclusive on the lower
side of the next band and exclusive on the upper side of the previous band, so
each case belongs to exactly one band. Empty bands are displayed as zero.

The calculation uses the first and last retained timestamp for each case with at
least one event, with microsecond boundary comparisons. One-event cases therefore
have zero **observed** elapsed time; the UI discloses how many there are. UTC
normalization from import applies; cases with no retained event do not appear.
Histories can be partial or open: the measured span does not prove that the real
case started or completed, how long an employee worked, whether an SLA was
breached, or why a case took its observed time. Equal timestamps and source
omissions also limit interpretation. The selected object type and existing global
whole-case filter apply to every band, and the selected/total counts are shown
when a filter is active. There is no sampling; percentages use the selected case
count and are unavailable when it is zero.

The local dashboard JSON download retains the case notion, scope, thresholds,
counts, percentages and observed quantiles. CSV summarizes band boundaries in
seconds and exact counts and shares; an empty upper bound means no upper limit.
Downloads can contain the log name and filter labels; review before sharing.
Changing thresholds removes the previous result and downloads until a new run
finishes. Neither download grants agent permissions or changes source data.

The dashboard-session endpoint is `GET /v1/event-logs/{log_id}/duration-distribution`
with optional `case_type`, the existing case-filter parameters, and a bounded
`cutoffs_seconds` comma-separated list of 1–10 strictly increasing whole seconds
from 1 to 3,155,760,000. The default is `3600,14400,86400,259200`. The same
band boundaries appear in the response as inclusive `lower_seconds` and exclusive
`upper_seconds`; `null` indicates an unbounded last band. The endpoint uses the
same local authorization and retention rules as the event-log workspace, returns
no event content or case identifiers, and never sends a notification or action.
