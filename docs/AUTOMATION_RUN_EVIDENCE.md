# Retained automation run evidence (R18)

In Evidence → Automation monitoring, select a deployment and choose
**Explore retained runs**. This is a review of previously imported reports,
not a live runtime console.

## Filter and inspect

Search by exact run ID, reported run status, exact step ID, step status,
inclusive start and exclusive end (timezone-qualified UTC-convertible
timestamps), and declared specification attribution. When both step filters
are supplied they must match the same recorded step occurrence. Each run is
counted once, including repeated step IDs. The matching step count includes
all retained steps of matched runs, not just the filtered steps.

Results include exact counts by run status, first reported failed/handoff
step (by original sequence), declared spec attribution, and an immutable
content reference for the current retained report. Open Inspect to see all
reported step occurrences, statuses, timestamps and the report's outcome
claim. This does not independently verify the runtime, its facts or its
business result.

The search computes a population digest over the filter, attached design
digest and all matching run and step records. Later pages require this exact
digest; a replacement report, new matching report or attachment change
invalidates it. Unrelated imports outside the matching selection need not
invalidate it. Reports are not historical immutable revisions: an import
can replace an existing run ID. The digest prevents silent substitution.
The detail request can require the selected run digest, which pins the
reported metadata and steps rather than current attachment metadata.

## Compare exact runs

Open two different retained runs and choose **Use as left run** and
**Use as right run**, then compare. In the dashboard both are selected from
the same deployment and current search population. The API additionally
supports comparing runs across two deployments.

The comparison finds one minimum insertion/deletion alignment on exact
reported step IDs, preserving repeated occurrences and both sides'
timestamps, labels and statuses. It exposes status changes for matched
IDs, differing labels, and steps appearing on only one side. Equal-cost
alignments may exist. Matching IDs do not establish equivalent business
tasks, case identity or verified causal effects. If either report was
replaced or deleted, the comparison refuses to substitute the latest one.

## API

All endpoints require the local dashboard session, use no-store responses,
and never invoke an agent, runtime, provider or business write.

- POST /v1/automation-monitoring/deployments/{id}/runs/search
- GET /v1/automation-monitoring/deployments/{id}/runs/detail?run_id=...&expected_sha256=...
- POST /v1/automation-monitoring/runs/compare

Search body fields: status, run_id, step_id, step_status, started_from,
started_before, attribution (any, attached_design, declared_other, unknown),
offset (default 0), limit (1–100), expected_population_sha256. The latter is
required for nonzero offsets; use next_offset from the preceding result.
Declared-other and attached-design filters require an attached design.
An absent report spec remains unknown, even when a design is attached.
A declared hash with no design is explicitly classified as no_attached_design.

Comparison body:

```json
{"left":{"deployment_id":"dep_0123456789abcdef","run_id":"a","run_sha256":"..."},
 "right":{"deployment_id":"dep_0123456789abcdef","run_id":"b","run_sha256":"..."}}
```

Use the exact returned 64-character digests. POST payloads are bounded at
16 KiB with strict JSON parsing. A search is limited to 50,000 matching
runs and 2,000,000 matching steps, otherwise it refuses rather than silently
sampling. Each run remains bounded to the existing 100-step report limit.
Searches use the existing local database lock; digest computation can be
expensive and no large-scale latency claim is made. No new persistent
storage is introduced.

Synthetic regression tests exercise exact occurrence filters, population
and detail invalidation, repeat IDs, comparison symmetry of input coverage,
unknown attribution, work limits and local authorization. No live provider
or customer runtime is certified by these tests.
