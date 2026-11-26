# Retained-log effort measurements (R16)

In **Automation pipeline → Realized value**, select a baseline or after
period and inclusive UTC dates, then open **Derive effort from a retained
event log**. Load the local logs and select a log, exact case type, final
completion activity, effort-bearing activity, numeric event attribute and
unit (seconds, minutes or hours). Confirm that the selected attribute
represents additive effort, not elapsed time, waiting, or an already
aggregated case total repeated on each event.

The preview selects every case whose first retained event falls in the
half-open UTC interval covering those dates. Cases beginning earlier are
not included, even when an event falls within the dates. Every selected
case must finish before the exclusive end boundary with the exact final
completion label and contain at least one effort-bearing event. Every such
event needs a nonnegative finite numeric value within bounds. A shared
effort event linked to multiple objects of the selected type anywhere in
the log blocks recording; the same effort is never allocated twice.
Raw retained labels are used, not hidden or relabeled presentation data.
The preview refuses populations above 250,000 retained events or selected
case links instead of sampling silently.

The preview reports complete/incomplete cases, missing activity/value
coverage, invalid values and shared-event attribution. Blocked previews
do not provide a partial effort total as if it were complete. Eligible
previews report total effort hours and average minutes per selected case.
These are calculations over supplied data with reviewer-declared semantics,
not independent verification of labor, source completeness, business
outcomes or causal savings.

**Record reviewed measurement** re-runs the preview and checks its SHA-256
against the reviewed result, then checks the idea revision before recording
in the existing measurement table. The hash covers the selected configuration,
log append revision, ordered case evidence and relevant values. The idea
revision is separately checked. Duplicate snapshots and overlap with any
existing measurement period on that idea are refused. The original log is
not changed. The measurement retains the source reference and compact
configuration note; deletion follows existing measurement deletion.
The saved record describes the historical analyzed snapshot, not a live
cross-store transaction or a permanent copy of the log.

## API

Dashboard session only, strict JSON and no-store:

- POST /v1/automation-pipeline/{idea_id}/retained-effort/preview
- POST /v1/automation-pipeline/{idea_id}/retained-effort/record

Both accept expected_revision, log_id, case_type, period, start, end,
completion_activity, effort_activity, effort_attribute, unit and
effort_semantics_confirmed. The record request additionally requires
expected_preview containing the preview SHA-256. Limits and existing
measurement period validation apply. No connector read, model call,
agent action or execution grant occurs.

Synthetic fixtures cover known numeric totals versus elapsed duration,
partial coverage, invalid values, changed preview pins, duplicate and
overlapping periods. Source-specific effort semantics and real-world
measurement validity still require independent customer review.
