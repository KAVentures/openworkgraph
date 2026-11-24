# Compare selected executions in Workflow Explorer

Open Overview → Explore observed workflows, select a candidate or exact variant,
and choose **Review selected examples**. The comparison workbench displays the
selected, freshly returned execution references, actor kind, elapsed duration,
projected step count and observed outcome status.

The occurrence matrix shows each structural token's support across the selected
runs and its repeat count per run. Choose a baseline and comparison to see
longest-common-subsequence support and the counts of unmatched projected steps.
Elapsed differences are shown only when both durations are known and actor kinds
match. Elapsed time is not active work time, productivity, or causal evidence.

The workbench validates exact requested/returned membership, refuses missing
or malformed step projections instead of treating them as zero, and discloses
source-limit uncertainty and the 40-distinct-step display cap. It compares at
most twelve existing selected executions and at most 160 projected steps per run.
Changing workflow, variant or period uses the existing evidence invalidation
and request-generation guards. There is no extra source query, persistence,
AI call, export or change to process design or recording.

Step absence is absence in the bounded **derived projection**, never proof of
an omitted business step; outcome labels are observations rather than verified
customer outcomes. Use canonical evidence and independent source verification
for factual conclusions.
