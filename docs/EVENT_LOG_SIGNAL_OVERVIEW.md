# R10 — All-log local signal review overview

OWG's saved event-log **Signals** panel already supports immutable typed
signal rules, explicit on-demand or opt-in scheduled evaluation, human
acknowledgement pinned to an exact state fingerprint, snoozing and reopening.
Those remain the canonical review mechanisms.

This feature adds a **single read-only operations view across saved logs**,
without creating a second rule store or approval workflow.

## Workflow

1. In the local dashboard, open **Activity → Large event logs**, select
   a previously imported log and choose **Analyse**.
2. In **Signals**, the **Across all saved logs** area displays the count of
   saved datasets with signal rules and their most recent review states.
   By default it shows results requiring attention: unreviewed, stale,
   never evaluated and unavailable/corrupt states.
3. Choose a state filter to inspect acknowledged, cleared or snoozed results,
   or all states. Counts cover the entire supported local inventory, even
   when the visible table is limited to 100 results.
4. Choose **Open log** on a result. The original log picker changes to
   the correct dataset, analyses re-render, and the existing per-log
   Signal review inbox opens. Only there can the owner acknowledge,
   snooze, reopen or explicitly re-evaluate a rule.

No one-click acknowledgement is available in this overview. A read of the
overview **does not evaluate** any source data and cannot cause external
messages, refresh jobs or business/agent actions.

## Endpoint and integrity

`GET /v1/event-logs/signal-overview?status=attention&limit=100`

This route uses the same authenticated local-dashboard session requirement
as `/v1/event-logs/{log_id}/signal-inbox`. An AI/MCP bearer cannot read it.

The server acquires the existing workspace lock and uses one DuckDB
connection for a bounded snapshot. It reuses the exact existing
`event_log_signal_inbox._state` revision and acknowledgement checks,
snooze matching and expiry rules, so:

- A no-op append still makes an earlier evaluation **stale**, rather than
  appearing acknowledged or current.
- An expired or invalid snooze cannot hide a state that requires review.
- A malformed persisted rule/evaluation is marked **unavailable**, not
  silently treated as cleared.
- Acknowledged, cleared and snoozed counts are distinct from the
  attention count. The result includes a bounded, explicitly truncated
  row list; no case IDs, evidence values, source records or review
  fingerprints are returned.
- Only local schedule metadata is consulted to show whether a saved
  signal has a configured periodic check. It never starts that job.

Supported limits: up to 50 local logs, 2,500 signal rules total, and
1–200 returned rows per request (100 in the dashboard). Invalid status
filters and excessive limits are rejected, rather than changing the scope
silently. The API response includes scanned dataset count, full state
counters, filtered matching count and truncation.

## Permissions and user-facing limitations

This is an on-device **operator dashboard**, not an organization-wide
multi-user alert service. It does not send external notifications, provide
delivery recipients, prove source freshness or grant execution rights.
A signal computed on a supplied historical CSV is not a live SLA alarm.
Correctness and source completeness remain dependent on authorized imports
and explicit or scheduled evaluation.

The table uses text-only DOM rendering for dataset names and signal names.
Selections and in-flight responses are invalidated when switching data
or filters, and an operator can navigate to the exact source log without
accepting a rule or authorizing any action.

## Acceptance

- `tests/test_event_log_signal_overview.py`:
  empty logs, two-log counts, bounded filtered rows, acknowledgement,
  snooze and expiry, duplicate-append staleness, source erasure,
  corrupt-state refusal, schedule metadata and authenticated
  session/bearer separation.
- `tests/browser/event_log_signal_overview.cjs`:
  two real CSV imports, cross-log current/unknown statuses,
  malicious label safely rendered, 100-row disclosure and filters,
  exact selected-log navigation, state-pinned acknowledgement through
  the canonical review interface, WCAG and mobile browser checks.
- The feature's own **path-filtered Chromium workflow** runs this
  acceptance without changing the existing global browser/CI suites.

Native CI and real Chromium acceptance must pass on the exact PR head
before claiming the feature merged or deployable. Synthetic fixtures
do not constitute a real multi-company operations pilot.
