# Compare communications response reports (R22)

Open the local dashboard, select **Evidence → Communications**, and use
**Compare two response episode reports**. This standalone browser workbench
uses two downloaded `openworkgraph.communications-response-episodes.v1`
aggregate JSON files. It does not import email/ticket bodies, retain cases,
access providers, create an event log or upload anything.

1. Produce the baseline and comparison aggregates with the existing response
   analysis, using a declared observation cutoff and one or more elapsed-time
   deadlines. Download the response episode aggregates from each analysis.
2. Select both files. Use the same selection, mapping, classifier/taxonomy and
   direction conventions. The workbench cannot certify those conventions or
   determine if the populations overlap.
3. Review both coverage summaries, common deadline thresholds, conditional
   percentage-point bounds, response latency, repeat contact, open age, elapsed
   episode-wait exposure and time-weighted observed backlog.
4. Expand exact shared intent/episode-kind groups. Only groups already visible
   and supported by at least five distinct threads in each source are matched.
   Groups missing or withheld on either side are not zero.
5. Download the minimized comparison JSON if desired. It includes SHA-256
   digests of the exact selected files, not authenticated signatures. Clear
   the workbench to remove the in-memory results and selected inputs.

The workbench checks aggregate consistency, including episode totals, deadline
partitions and monotonicity, distinct-thread support, visible group accounting
and workload totals. It recomputes shares rather than trusting provided shares.
Both files must be distinct and have common thresholds. No arbitrary raw report
fields are forwarded. The 8 MiB per-file limit bounds browser processing.

For each common deadline, with observed on-time count m, unresolved-within-
deadline count p, and episode count n, the conditional sample on-time share
lies in [m/n, (m+p)/n]. The possible change between comparison B and baseline A
has endpoints lower(B)-upper(A) and upper(B)-lower(A). The direction uses exact
integer cross-products; floating point is used only for display. This is not a
confidence interval or an estimate correcting for missing messages. Source
population disjointness, classifier comparability, completeness, actual
resolution, staffing productivity, and causal changes are NOT verified.
Do not use the report to certify an SLA or make individual employment decisions.

Validation: pure comparison checks with 500 exhaustive pending-outcome oracles
and deterministic malformed-report, support, duplicate-digest and unmatched-
group cases. The full browser acceptance checks the actual exported report
format and scope clearing; run native/full CI before merging. Existing
communications analysis and retention options are unchanged.
