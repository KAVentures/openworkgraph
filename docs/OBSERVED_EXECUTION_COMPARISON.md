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

## Selected-run process paths and reviewed design handoff

The workbench groups selected executions by actor type and identical returned
structural sequence. It shows direct-follow transition occurrences and
supporting run IDs, as well as tokens with multiple observed immediate
successors. These are descriptive observations, not verified branching rules
or parallel-process semantics. Select one path and explicitly review it to
open an unsaved AS-IS process catalog proposal with only that path's retained
execution references. Limited source coverage, unknown actor type and paths
over the existing 40-step catalog limit block this handoff. Reaching the
48-step structural projection cap is disclosed; later steps may be missing.
The catalog's existing review and save requirements remain unchanged.

## Directional clipboard linkage

The evidence summary and navigation ranking now use the same directional
clipboard pairing logic. A paste requires a strictly earlier copy or cut of
the same transfer token in the same capture session, device and actor scope.
A reused token is associated with the latest unambiguous preceding source,
not a future copy. Simultaneous source/paste observations do not establish
ordering; tied multiple sources leave the origin ambiguous. Missing capture
scope or missing source events may reduce support. Clipboard payloads are
never inspected and a linked occurrence does not establish business-case
membership or prove completion.

## Selected-path agent planning

The selected-path panel also accepts the user's intended business goal,
authoritative source, proposed decision rules/exceptions and independent
outcome check. After reviewing these inputs, copy a read-only planning
request for an external AI. It carries only the selected path's execution
references, evidence window and user-proposed criteria, not captured labels
or event contents. Editing criteria or changing paths clears sharing
confirmation. This is not a grant, an execution request, or a verified
outcome; use the existing agent implementation and authorization workflow
for any subsequent action.

## Per-execution foreground coverage

The workbench reports retained event counts and wall-clock foreground coverage
within each inferred human execution window. It clips focus spans to that
window and unions overlaps instead of adding duplicated durations. Intervals
with more than one distinct surface token are reported as ambiguous overlap,
not attributed twice. The remaining window is unknown time: not idle,
waiting, billable hours or labor. Runs may themselves overlap; adding their
coverage does not measure unique work time.

Select a run to examine exclusive surface attribution, ambiguous overlap,
and ignored invalid spans. Missing foreground evidence is reported as
unobserved, not a measured zero. For agent runs, foreground coverage is
not applicable. Only already-selected evidence is used, so spans outside
the retained event scope can be missing. At most 30 surface rows are
displayed; the remaining exclusive seconds are preserved separately.
The evidence response adds an optional versioned timing projection without
changing existing timing fields or requiring additional source queries.

## Observed execution → saved process-mining log → reviewed model (#264/#326/#370 R5)

After inspecting explicit executions, select up to 12 opaque execution IDs,
a snapshot name and an observation projection. **Interactions only** retains
coarse human actions and structural agent operations; **Interactions +
navigation context** also retains allowlisted human foreground changes, page
views, navigation, tab activation and page-show observations. Both use actual
retained event timestamps, opaque evidence references, fixed activity
categories and privacy-safe surface tokens. Neither copies captured text,
clipboard payloads, URLs, window titles or native tool inputs. Observations
are not deduplicated physical actions; tied timestamps do not prove order.

The authenticated local dashboard previews the exact scope, case/event
counts and content digest. Explicit local-storage confirmation repeats the
projection and refuses a changed digest. Source cap (25,000 rows), event cap
(10,000 projected observations), missing execution references, ambiguous
overlapping activity evidence or empty cases block creation rather than
silently importing a partial selection. Default-mode digests remain
compatible; the richer projection has a distinct identity. Same-name exact
duplicate creates reuse the retained dataset. Original event rows cannot
be appended to; create a separate new dataset for additional observations.

The resulting independent log is stored in the existing DuckDB workspace
and opens by exact log ID. It supports existing process-mining analytics,
model discovery and review. It does not represent verified business-case
identity, complete capture or independently confirmed outcomes. Ordinary
log analytics do not load raw observation history. If the source manifest
is unavailable, the log remains analysable with that status disclosed.

**Original observed execution snapshot** in the saved-log overview can
retrieve the bounded original manifest without reading observation history.
A separate action compares the original projection digest to the currently
retained evidence, reporting matches, changed or unavailable. This checks
the structural projection at a point in time, not business truth or
continuous monitoring. An explicit per-execution link opens the original
retained evidence window in the explorer; unavailable history is never
silently replaced by another run. Independent snapshots survive source
retention changes until deleted separately. Deleting a log removes its
manifest in the existing deletion transaction.

The existing model-discovery source snapshot additionally pins the original
observed-execution digest and counts when present. The reviewed catalog and
automation pipeline can reopen that exact retained log. Handoff review
packets carry only minimized original-observation digest and status, never
raw execution IDs or source content. Original-history revalidation is
separate from the local log fingerprint and remains explicitly unverified
in those packets. A missing/changed manifest adds an outstanding review
blocker, not an automatic rejection or approval of business actions.

Endpoints:
- POST /v1/workflow-evidence/event-log/preview
- POST /v1/workflow-evidence/event-log/create (explicit storage header)
- GET /v1/workflow-evidence/event-log/source?log_id=…
- POST /v1/workflow-evidence/event-log/recheck

All four require an authenticated local dashboard session; connected AI
bearer tokens cannot use them. No new collector, outbound sharing or
execution grants are introduced. The original event-log workspace, process
catalog, opportunity pipeline and agent project artifacts remain the
existing implementation paths.

Regression coverage includes exact snapshot selection, real timestamps,
mode changes, overlap refusal, stale preview, duplicate save, malformed
metadata, source drift/unavailability, local authorization, deletion,
model-discovery lineage and minimized project review. Full cross-platform
CI and real consenting pilot acceptance remain separate gates.

### Exact event drill-down from process-mining findings

The case-event inspector now offers an explicit **Recheck displayed
observations** action for pages of up to 100 original saved event IDs.
The request pins the retained log and exact inferred execution; event IDs
from another case, duplicates, missing IDs and unsupported identifiers
are refused. The server reads original saved rows before analysis activity
mappings, projects retained evidence within the original bounded scope,
and compares exact opaque evidence references, execution membership,
activity and timestamp. Results distinguish matching, changed, absent
from current projection and source unavailable. No source payload or raw
identifier is returned, and neither the log nor capture is changed.
The new POST /v1/workflow-evidence/event-log/observations endpoint requires
the authenticated local dashboard session. Source rechecks are explicit,
not automatic or a verification of business outcomes.
