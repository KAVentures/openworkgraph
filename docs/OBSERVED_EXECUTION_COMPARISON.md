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
and request-generation guards. The comparison and route views make no extra source query. The individual-run
chronology explicitly requests one existing evidence bundle. No persistence, AI
call, export or change to process design or recording is introduced.

Step absence is absence in the bounded **derived projection**, never proof of
an omitted business step; outcome labels are observations rather than verified
customer outcomes. Use canonical evidence and independent source verification
for factual conclusions.

## Discovery and cross-application analysis

Choose structural specificity or cross-app evidence ranking, require two, three,
five or ten observed repetitions, and optionally require multiple surfaces.
Changing a discovery setting clears the previous selection and invalidates
in-flight reads. Select Explore workflows to apply the new filters using the
existing candidate-discovery endpoint.

The cross-application map shows ordered surface transitions, distinct supporting
executions, repeated transition occurrences, and separately reported linked
clipboard-transfer summaries. Generic browser and opaque surface tokens break
the identifiable route rather than bridging unrelated apps. Navigation is
not a data transfer or verified business handoff.

## Individual run chronology and automation review

Select an execution and explicitly load its retained chronology (up to 160
events). Filter by app or event type. The display includes timestamps and
opaque evidence references, but not titles, contents, coordinates or metadata.
A response must match the selected execution and coverage counts; stale
responses are ignored. A bounded inferred human event window is not an
authoritative case trace.

Automation opportunity review surfaces repeated actions within multiple runs,
shared projected steps, path variation and linked cross-app transfers. These
are candidate investigations, not estimated savings, causal findings or
execution authorization. Continue using the existing process design or safe
agent handoff workflows to review the business goal, decision rules, live
tool mappings and independent outcome verification.
