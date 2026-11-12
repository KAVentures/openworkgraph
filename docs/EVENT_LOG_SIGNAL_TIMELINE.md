# R10 — Across-log signal transition timeline

OWG already has per-dataset saved signal rules, exact-state review and a
cross-dataset overview of **current** review status. The local signal
**transition timeline** now answers a different question: *which saved checks
recently changed from clear to triggered, or from triggered to clear, across my
saved logs?*

## Dashboard

In **Activity → Large event logs → Analyse → Signals**, below **Across all saved
logs**, open **Across-log signal change timeline**. It loads the retained
transition journal for every imported log and shows evaluation time (UTC),
dataset, rule name and transition. Filter to all, triggered or cleared. Choose
**Open log** to switch to the correct source dataset and inspect the original
signal inbox. The timeline never acknowledges, snoozes or re-evaluates a rule.

The counts are computed across the complete retained *local journal*, before
limiting visible rows; the UI shows up to 100 recent matching transitions.
Ties are ordered by the stored transition ID. A filtered or truncated list
must not be read as exhaustive. The maximum API page size is 200. This is
**not** a live notifications service and **not** the current review state:
a historical trigger may already have cleared, become stale, or been reviewed.

## Local API

`GET /v1/event-logs/signal-timeline?transition=all&limit=100`

- `transition`: exactly `all`, `triggered` or `cleared`; invalid filters
  are rejected.
- `limit`: integer 1–200; no unbounded results.
- Response `format`: `openworkgraph.signal-transition-timeline.v1`,
  `counts.triggered`, `counts.cleared`, `retained_total`,
  `matched_transitions`, `returned_transitions`, `truncated` and
  `items`. Each item contains only random event ID, dataset ID/name, saved
  rule ID/name, change and evaluation time. No matching cases, source records,
  live credentials, raw event contents or state fingerprints are returned.

This endpoint uses the same local-dashboard session requirement as the
existing across-log overview. An AI/MCP bearer cannot read it. The server
queries the existing DuckDB journal under the workspace lock, joins only
retained logs and rules, and performs **no source access, evaluation, outbound
delivery, agent dispatch or business write**. Removed source rules/logs are
not exposed through stale orphan journal rows. Corrupt stored rule names are
shown as unavailable rather than interpreted as markup.

The existing journal retains at most 5,000 transitions per dataset. An initial
false evaluation does not create a fake clearance. Repeated evaluations that
leave the triggered boolean unchanged do not create duplicate transitions.
There is no historical backfill before signal rules existed, and old retained
history may already have been trimmed.

## Acceptance

- `tests/test_event_log_signal_timeline.py`: empty inventory; exact counts
  before pagination; mixed triggered/cleared filters; cross-log ordering;
  no case data/state fingerprints; source deletion; corrupt rule definition;
  invalid bounds; real session-vs-bearer HTTP access and read-only method.
- `tests/browser/event_log_signal_overview.cjs`: actual two-log Chromium
  import, safe text rendering of a hostile saved name, triggered/cleared
  filtering, navigation back to the source log, WCAG and mobile checks.
- Full CI must pass on the exact proposed PR head before merge.

This is local product functionality, not a real-provider reliability test,
alert-delivery SLA, organizational monitoring service or an authorization to
execute work on a triggered signal.
