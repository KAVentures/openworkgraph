# Dotted-chart legend bounds

The dotted chart chooses its seven colored activities by whole-log event
frequency, with activity label as the tie-breaker, so filtering does not change
colors. It fetches only those seven labels into the application. For the
selected cohort, their event counts are computed directly, and the “Other”
entry reports the exact number of remaining events and distinct activities.

This bounds the legend rows returned to application memory. DuckDB still scans
and groups the relevant events to calculate the ranking and counts. The
existing case sampling and within-case point-thinning limits remain unchanged;
this is not a constant-memory guarantee for the full query.
