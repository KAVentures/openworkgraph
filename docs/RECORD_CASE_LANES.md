# Exact-record observation lanes

This is an additive, read-only reconstruction layer for observed work on
**the same opaque OWG resource reference** across interleaved activities.

## What it does

- The existing `/v1/workflow-evidence/families` response now contains a
  `record_case_lanes` field in addition to `families`,
  `candidate_clusters`, and `record_linked_activity`.
- Multiple observations of the **same** opt-in `owg:r:...` reference
  are projected into a timestamp-ordered lane even if other business records
  were opened between them.
- Distinct records never merge through nearby timestamps, repeated labels,
  copy/paste coincidence, or inferred family membership.
- Disagreements in provider, resource type, host, or organization identity
  cause the entire conflicting lane to be withheld.
- The Overview `Teach your AI from observed work` section now includes a
  collapsible `Record-linked observations` view. The helper falls back to
  the current `#patternCard` when no legacy repeated-workflows card exists.
- This uses existing locally authenticated evidence and does not call any
  outside source or AI model.

## Safety boundaries

- No customer titles, native case identifiers, browser URLs, clipboard contents,
  typed text, arbitrary UI strings, raw actor IDs, or raw event IDs are added
  to the projection. Timeline rows include only timestamp plus sanitized
  structural event type and sensor source.
- Actor/session **counts** are descriptive; multiple actors on one record
  do not prove a handoff.
- Repeated events across many days do not prove a single business process
  instance, a start-to-finish case, or a successful outcome.
- Both the backend and UI honor existing limits: maximum 30 lanes and
  24 steps per lane in the returned model; source-event truncation is
  separately exposed by the existing evidence endpoint.
- There is no new capture permission, database, migration, source connector,
  background sync, business write, or AI execution authority.

## Limitations and next steps

- Records not identified by the original opted-in sensor are invisible to this
  view even when a person worked on them.
- Process-mining joins from authoritative business event logs, cross-record
  customer case correlation, exact workflow boundaries, and human-validated
  process models remain future work.
- The initial view uses existing evidence-derived timelines, rather than
  arbitrary user-entered workflow names or automatically guessed identity.

Regression: `tests/test_record_case_lanes_v0135.py` checks interleaving,
identity conflicts, organization boundaries, duplicate evidence, private data,
bounds, and existing workflow response compatibility.
