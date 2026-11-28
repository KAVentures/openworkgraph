# Rule-level signal history (R10)

Open Evidence → saved event log analysis → signals → Historical signal episode
review → **Rule-level deadline and recurrence review**. Select a completed
UTC date interval (exclusive end, maximum 366 days) and enter positive
whole-second clearance and recurrence thresholds. Optionally select an exact
log ID. The review uses the same bounded retained transition population as
the existing episode review (100,000 transitions, refusal rather than partial
summary). No new history is generated.

The dashboard lists rules by recorded late clearances plus unresolved triggers
whose window extends past the threshold, by adjacent retriggers within the
chosen horizon, or by retained trigger volume. All and matching-scope totals
cover the entire window before pagination. Every retained trigger is assigned
to one of: observed clear within threshold, observed clear after threshold,
unresolved with window past threshold, unresolved with window not past threshold,
interrupted, or ambiguous. Clearances are accounted separately. Exact microsecond
elapsed comparisons preserve equality boundaries. Adjacent retriggers require
a cleared pair followed by an unambiguous trigger on the same rule, with no
intervening evidence row. Counts and min/median/max are descriptive only.

Use **Inspect exact rule episodes** to drill into the existing episode evidence
view with the original population digest and exact identifiers. Paging pins
both the retained source and the review assumptions; changes refuse the request.
Download exports one current page plus whole-window totals and the source pin.
No source record, raw rule definition, state fingerprint or new persistence
is introduced.

Rules without retained changes are absent, not healthy. Ties, leading clearances,
interrupted triggers and incomplete windows are not repaired. Elapsed duration
is between retained evaluations, not actual outage or incident duration;
unresolved past-threshold rows are not observed late clearance pairs. Ordering
does not establish operational severity, adopted SLA compliance, actual risk,
or individual performance. No evaluation, acknowledgement, notification, live
provider check or execution occurs.
