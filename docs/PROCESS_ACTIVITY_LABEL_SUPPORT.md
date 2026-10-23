# Inspect labels on an actual activity

For an explicitly authorized participant/team or shared-key import, select an
activity in **Explore all observed transitions**. **Labels on this activity**
shows event counts and distinct-case support by supplied participant label,
team label and source alias, for whichever dimensions were selected in that
import. The same section appears when inspecting an activity inside an exact
selected variant.

The combined inspector uses every occurrence of that activity in the current
whole log or complete-case cohort. The variant inspector uses every matching
case and its full path in that exact variant. Statistics are computed from
selected event rows, not the 30-case preview, five variant examples or 24-event
displayed path. A late activity in an undisplayed case still contributes.

A case may contain several participant/team/source labels or both known and
missing labels on different occurrences of the activity. Label case-support
counts are therefore not additive and do not describe disjoint populations.
Missing labels have separate event and case counts; they are not invented as
one shared unknown person/team/source. Ordinary structural logs gain no
attribution panel or manufactured identity when no labels were authorized.

A label-filtered cohort still includes complete cases. Other labels from those
same cases can appear in the activity inspector, including events outside the
case's date-selection window. No event clipping narrows the history to the
selected actor or shortens a workflow. Labels retain their original import
aliases; they are never joined to people, membership, providers or other logs.

Each dimension displays up to 20 labels ranked by event count, then literal
alias. The total label count, event/missing counts and truncation indicator use
all labels and all selected cases. Source aliases are already bounded to four.
Counts describe supplied event labels; they do not independently verify people,
team membership, source identity, business intent, effort or human handoffs.
They never assign workflow ownership or process responsibilities automatically.

## Projection and compatibility

Optional `label_support` metadata on full-log and per-variant activity rows
contains one entry per authorized dimension: `participant`, `team`, `origin`.
It includes known label event counts, missing-label event/case counts, distinct
label count, returned/truncated row counts and bounded
`{label,event_count,case_count}` rows. The basis is
`supplied_labels_on_this_activity_events`; case-additivity, identity/membership
and effort/handoff verification flags are explicitly false.

The existing CSV miner computes both populations from full sorted histories.
No new source read, AI call, permission, table or identity mapping is introduced.
The same local-dashboard/rich-import authorization and retention controls apply.
Normal structural-import response rows remain unchanged.

Previously retained label datasets can have an older cached report without
these derived counts. Opening such a dataset recomputes the projection from
its already-authorized retained case/event rows in memory. It preserves
original case/key aliases, path/variant identity and original input-quality
diagnostics, without rewriting the snapshot, cached report, evidence or process
model. Existing imports that never retained label columns remain unattributed.

Verification covers repeated occurrences, overlapping per-label case support,
missing labels, global-versus-variant populations, source-only/structural logs,
40 labels beyond preview caps, activities beyond event 24, 20-label display
bounds, complete-case date selection and read-only old-cache regeneration.
Actual browser flows inspect source support across a full log and a selected
variant, and inspect participant/team aliases without revealing native names
or inventing source attribution. Existing native-key minimization, retention,
reopening, complete case browsing and mobile checks run in those same flows.

[Open matching cases](PROCESS_ACTIVITY_LABEL_CASES.md) from a retained label
row or missing-label summary. Matching requires the label and activity on one
event and preserves the current cohort and optional exact-variant scope.
