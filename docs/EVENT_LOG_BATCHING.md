# Batch processing (R14)

Some work is done in batches. A clerk approves twenty invoices in one sitting; a nightly job
exports every order at 23:00. Batching makes cases wait to be batched, and a simulation that
treats every case one by one will understate that wait. **Batch processing** in the
event-log workspace finds such batches in a saved log.

## How batches are found

Within one activity, events by the same resource (or, with *Separate by resource* off, by
anyone) are ordered by time. Events that follow each other within the **gap** (default 60
seconds) form a run, and a run of at least the **smallest batch** size (default 3) is a
batch. There are two kinds:

- a **simultaneous** batch has every event at one instant, as from a scheduled job;
- a **sequential** batch is one event after another in quick succession, as from a person
  working through a pile.

An event linked to several cases counts once. Without recorded resources, batches are found
per activity.

## The report

For every activity:

- the share of its events that were in batches;
- the number of batches, split into simultaneous and sequential;
- the batch size (median, 90th percentile, maximum) and the median time from a batch's first
  to its last event;
- the median **wait before** a batched event, from the case's previous event, next to the
  wait before an unbatched event. This is a rough sign of time spent waiting to be batched.

Choose **View batches** to see an activity's inferred batches, newest first. **Inspect cases**
shows the cases contributing to a batch, with their event counts and time ranges within the batch.
**Open case** shows the recorded event history. The active case filter applies to all three views.
A shared event counts once toward the batch size and can appear under each case it belongs to.

Each batched activity also gets a **simulation proposal**: the median batch size and, as the
maximum wait, the 90th-percentile wait before batched events. Enter it as that activity's
`batch` setting in [what-if simulation](EVENT_LOG_WORKSPACE.md) if it fits. It is never
applied automatically (`proposals_applied: false`).

Batches are inferred from timestamps and resources, so they are not proof that work was
deliberately batched. Resources are pseudonymous labels, never ratings of people.

## API (dashboard session only)

`POST /v1/event-logs/{log_id}/batching` with
`{"gap_seconds": 60, "min_batch_size": 3, "by_resource": true, "cohort": {...}, "case_type": "..."}`.
All fields are optional.

`POST /v1/event-logs/{log_id}/batching/batches` uses the same settings plus the exact `activity`.
Send a returned `batch_id` with those settings to `POST /v1/event-logs/{log_id}/batching/batches/cases`
to get its cases in literal case-key order. Both endpoints take an optional `limit` of 1–100
(default 50). Pass the returned `next_after` as `after` to continue. Each page reflects the
current log.

## Downloads

**Download JSON** saves the summary shown. **Download CSV** has one row per activity, including
activities with no batches. Each row gives:

- events, batched share, batches and simultaneous batches;
- median, 90th percentile and largest batch size;
- the median wait before batched and unbatched events, in seconds;
- the simulation proposal's batch size and maximum wait in minutes.

Changing the gap, the smallest batch or the resource grouping disables the download until
batches are found again.
