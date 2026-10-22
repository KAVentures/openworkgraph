# Review observed changes between event logs

Open **Evidence → Process mining · event logs → Retain or reopen a local
event-log snapshot**, then **List saved local logs**. Choose two different
snapshots as the baseline and current logs, declare that they describe the
same process with compatible activity labels, and select **Compare retained
logs**. File selection, listing and selection of comparison inputs do not run
a comparison or retain another copy.

The comparison uses every retained event and complete case path from both
snapshots, including cases and variants omitted from preview samples. It shows:

- Paths observed only in one snapshot, changes in each path's case share, and
  the median elapsed duration for the same full path in each log.
- Activity event counts and distinct-case incidence. Repeated activity events
  remain separate from the number of cases containing that activity.
- Adjacent transition counts, case incidence and median elapsed gaps, including
  self-loops and repeated transitions.
- Overall median and nearest-rank p90 case elapsed durations and descriptive
  total variation distance between the full-path case distributions: zero for
  identical observed mixes and one for disjoint observed mixes.

There is no significance test, threshold-based drift verdict or claim that the
current log caused an improvement. Unequal case volumes, different populations,
log coverage, changing activity labels, mix changes and observation windows can
explain apparent differences. Within-path timing summaries help distinguish
observed composition changes, but do not control for business complexity.
Elapsed durations and gaps are not active labor, verified waiting or savings.
Single-event cases legitimately have zero elapsed duration. Percentage timing
changes are unavailable when the baseline median is zero or a path/transition
was absent.

Case and variant ordinals are local to each snapshot. They are never matched
across logs. Literal full activity sequences establish path identity; complete
path fingerprints distinguish paths with identical displayed prefixes. Labels
are not normalized into inferred business activities. Comparability is a user
declaration, not an independently established equivalence. Native case IDs were
discarded at retention, so duplicate cases across snapshots cannot be ruled out.
Overlapping event windows, tied timestamp ordering and small case counts receive
explicit advisory messages. The fewer-than-20-cases advisory is not a statistical
significance threshold. Original input-quality diagnostics are preserved.

The response displays up to 50 paths and 100 transition types, ranked by absolute
case-share change, then combined support, then literal key. Path labels display
up to 24 steps and retain their full event count and SHA256 fingerprint. Summary
metrics use all paths, including omitted rows. Activity rows include the complete
union (at most 128 labels across the two separately bounded imports).

## Connect to a saved process

Select **Compare next CSV with this saved revision** on a catalog design before
requesting the log comparison. Both snapshots are structurally compared against
that same immutable process revision, identified by workflow ID, revision and
content SHA256. The report includes exact structural-match counts and the change
in match share. Business rules, branch conditions, policy compliance and source
outcomes are not evaluated. An exact structural match does not establish any of
those facts. A newer catalog revision does not silently replace the reference.

**Inspect baseline snapshot** and **Inspect current snapshot** reopen the
existing explorer and complete case/event browser. Review a supporting variant
and explicitly draft and save a proposed improvement through the existing
designer. Comparison never rewrites a process, creates a review/approval, deploys
an agent or grants business execution authority. Changing selected logs,
comparability, reference revision, deleting or refreshing the log inventory
clears the report and invalidates pending comparison responses.

## API and storage

`GET /v1/process-mining/datasets/compare` requires a valid local dashboard
session, distinct `baseline_id` and `current_id`, and
`comparability_confirmed=true`. Optional `workflow_id` and exact `revision` must
appear together. Ordinary AI bearer credentials cannot read retained logs or
this comparison. Responses use `Cache-Control: no-store`; inputs are bounded
opaque local snapshot IDs. No source-system, model-provider or business-tool
requests occur.

Dataset metadata and all event rows are read in one SQLite snapshot so concurrent
deletion cannot yield a partial case history. Both logs must exist and be
unexpired when the read snapshot is acquired. Each returned reference includes
dataset ID, retention dates, counts and a SHA256 of the ordered minimized event
rows. Comparison is ephemeral; it creates no tables, retained report, policy
decision or additional evidence. Existing expiry pruning remains active.

The source logs retain the existing bounds of 1 MiB, 5,000 events, 1,000 cases
and 64 activity labels each. This feature compares explicitly retained local
snapshots. Continuous source monitoring, authoritative cross-system case joins,
business-rule drift, large-volume company analytics and independently verified
business improvements remain separate development and acceptance work.

Validation covers full unsampled histories, frequency-rank changes, identical
display prefixes, repeated transitions, zero baselines, population-volume
differences, reversed comparisons, exact model revisions, concurrent deletion,
expiry, authentication and pending browser responses. The real browser flow
compares two owned synthetic snapshots with a change after event 130, then
reopens the current log, verifies privacy boundaries and deletes both snapshots.
REA static analysis of the updated JavaScript supports code inspection; bounded
static analysis is not runtime certification.

Analysis artifact: `/tmp/owg-rea-drift-analysis.json`, evidence
`ev_a0773cd8b2baa4195c083aec099b1e74851e8ed090643ce0378b14463da61d5c`.
The artifact is a development-workspace file, not part of the distributed
product. It analyzes the selected source file only; other files are outside
that invocation's scope.
