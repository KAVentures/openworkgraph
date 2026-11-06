# Interaction detail (capture depth)

How much interaction detail the browser extension records on this computer is your
choice. Set it under **Privacy → Interaction detail**.

| Profile | Adds to today's signals |
|---|---|
| **Standard** (default) | nothing: clicks, focus, changes, copy/paste, submits and navigation, as before |
| **Enhanced** | hover dwell on controls, drag and drop, friction candidates |
| **Full fidelity** | Enhanced plus sampled pointer movement; needs its own acknowledgement |

You can also switch each signal on or off on its own, set how long the pointer must
rest before a hover counts (300–5,000 ms, default 750), and set pointer samples per
second (1–20, default 4). The extension also caps all of these events at 60 a minute.
How long this detail is kept is set separately (below).

**Time-limited.** Enhanced and Full fidelity are never open-ended. Each time you turn on
extra detail you choose how long it lasts (1, 7, 30 or 90 days; the API takes
`duration_days`, 1–90, default 7). When the period ends, the local server and the
extension both return to Standard by themselves. The Full fidelity acknowledgement is
cleared whenever pointer movement is turned off or its period ends, so turning Full on
again always asks again.

Capture depth only decides what is **recorded locally**. Retention, sharing with your
organization and sharing with an AI or cloud service keep their own separate
settings: work text, screenshots, AI access, MCP tools, Gateway sync and exports.
Changing capture depth never changes them.

## Signals

**Hover dwell** (`browser_control_hover_dwell`)
- Recorded for buttons, links, menu items, tabs, options and similar controls only.
- Each event has the control's tag, role and type, the duration rounded to 50 ms,
  and how the hover ended: the pointer left, the control was activated, or the page
  was hidden.
- Not recorded for quick passes, disabled controls, touch input, password or other secure
  fields, controls inside a secure widget (for example a "show password" button), or
  anything under the pointer that sits over a secure field.
- The same control is not recorded twice within two seconds.

**No labels.** Hover and drag events record no label of any kind, only the control's
tag, role and type (`secure` for secure fields), and `editable` for editable elements.
Element text, `aria-label`, `title`, `placeholder` and `<label>` text are all left out,
because a page can fill any of them from what the user typed or from the user's own
content. The server enforces the same rule at ingest, so a label sent by any client is
dropped from these events.

**Drag and drop** (`browser_drag_start`, `browser_drag_drop`, `browser_drag_end`)
- Records the source and destination controls and the declared payload *kinds*
  (`files`, `text`, `html`, `uri`, `other`).
- Never records the dragged data, text, HTML, file names or file contents.
- Drags from or onto a secure field or widget are not recorded.
- A drag that ends without a drop is `incomplete`, and a drop from outside the page is
  `external`.

**Pointer movement** (`browser_pointer_trajectory`, Full fidelity only)
- Samples at the chosen rate, as fractions of the viewport (0–1, three decimals) with
  relative milliseconds and a coarse viewport size.
- Sent in batches of at most 200 points or 10 seconds.
- No samples are taken while the pointer is over a password, one-time-code, card or
  other secure field, inside a secure widget, or over a secure field hidden under another
  element (the check covers everything stacked under the pointer, not only the element
  that receives the event). The current batch ends at the field,
  and sampling resumes when the pointer leaves it.
- The raw points are kept on this device in their own table
  (`pointer_trajectory_points`), apart from the event rows, and are deleted with their
  event. The stored event carries only a point count and duration
  (`raw_points_stored_locally: true`, `raw_points_shared: false`). No event read or
  export returns raw coordinates: not the workflow trace, the events API, MCP (Redacted or
  Full), workflow-evidence bundles in either representation, the history export, or
  JSON, XLSX and CSV-zip exports with or without `redact_names`. Those output boundaries
  also summarize any trajectory that still carries points.
- **Local backups do include them.** A backup (Privacy → Back up recorded activity) is a
  copy of the whole local database, so it contains the raw points, like everything else
  recorded on this computer. Without a passphrase the backup file is not encrypted. Restore
  is unchanged.

**Friction candidates** (`GET /v1/capture-coverage/friction`) are patterns in observed
events, for a person to review:

- the same control activated 3+ times within 2 seconds;
- going back to the previous page within 15 seconds;
- dwelling on a menu item with no activation within 5 seconds;
- more than 10 seconds between activating a control and the next page view.

They are never read as attention, confusion, intent or performance, and there is no
per-person scoring.

Hover dwell and pointer movement are context only. Workflow reconstruction does not
treat them as steps, or as merge evidence by themselves.

## Enforcement

The local server enforces capture depth at ingest, so a stale extension cannot store a
disabled signal. It also:

- refuses high-fidelity events with malformed or oversized metadata;
- stores no high-fidelity events on pages you have excluded;
- keeps only tag, role and type for the targets of hover and drag events;
- drops the keys reserved for high-fidelity data (`points`, `trajectory`,
  `pointer_points`, `sample_hz`, `viewport_bucket`, `coordinates`) from every other
  browser event, so coordinates cannot ride along in an ordinary click;
- returns to Standard once the chosen period ends.

The extension falls back to Standard when the local server cannot be reached or the
period has ended.

**Trust boundary.** These rules bound what the paired browser extension can store. The
extension is a trusted, paired client: one that has been modified can still put other
content in fields that ordinary events are allowed to carry. That is the same boundary
as for all browser capture, not a property of capture depth.

## Coverage manifest

`GET /v1/capture-coverage` lists each capture modality as `on`, `off`, `unsupported`
or not seen, with counts of high-fidelity events over the last seven days. It covers
browser structural events, hover dwell, drag and drop, pointer movement, desktop
clicks, desktop hover, drag and pointer (unsupported for now), screenshots and work
text. Reconstruction and agents can use it to tell a missing signal from an absent
behaviour.

## Keeping and deleting interaction detail

- **Retention.** Hover, drag and pointer events are kept for *Keep interaction detail*
  days (1–365, default 30). Older ones are deleted, along with their derived rows and
  stored pointer points, when new events arrive (at most once an hour). Clicks, pages
  and other structural activity follow the ordinary history retention instead.
- **Delete now.** *Delete interaction detail now* removes every recorded hover, drag and
  pointer event from this computer and keeps all other activity
  (`POST /v1/capture-depth/delete` with `{"confirm": true}`, optionally
  `"older_than_days": N`).
- **Pointer heatmap.** *Show pointer heatmap* is a local review aid. It lists the pages
  with sampled pointer movement from the last 7 days, then shows where the samples fell
  on the chosen page as a 20 × 20 grid of counts. Only the counts reach the dashboard,
  never the points. It is not a measure of attention, effort or performance.

## Performance

`tests/test_capture_depth_scale_acceptance.py` seeds an isolated history and times what
high-fidelity capture adds. A small history (6,000 events) runs with every test run. The
300k-event acceptance, OWG's existing large-history bar, runs in the scale-acceptance
workflow (`OWG_SCALE_ACCEPTANCE=1`). It has 270,000 structural events and 30,000
high-fidelity ones (10,000 each of hover, drag and pointer batches of 40 points) over 60
days, half of them older than a 30-day retention limit.

Measured in this repository's CI-like container:

| Step | 300k-event history | Budget |
|---|---|---|
| High-fidelity ingest | 1.3 ms per event | < 20 ms |
| Coverage manifest (90 days) | 0.31 s | < 30 s |
| Friction candidates (31 days, bounded to 20,000 events) | 0.96 s | < 30 s |
| Heatmap page list / one page | 0.18 s / 0.19 s | < 30 s |
| Retention purge (14,980 events) | 1.8 s | < 120 s |
| Delete interaction detail now (15,020 events) | 1.4 s | < 120 s |

Retention and delete-now remove exactly the high-fidelity rows and their stored points,
and keep every structural event. Friction candidates say when their 20,000-event window
was truncated. CPU, memory and battery use of long-running capture on real devices are not
covered by these tests.

## Not yet done

- Desktop hover, drag and drop and pointer movement on macOS and Windows: the
  manifest lists them as `unsupported`.
- Iframes from other origins are covered only where the content script already runs.
- The issue's ablation evaluation, on held-out interleaved workflows, has not been run.
  No reconstruction accuracy gain is claimed until it has.

## Routes

| Route | Purpose |
|---|---|
| `GET /v1/capture-depth` | Current profile, signals, parameters |
| `PUT /v1/capture-depth` | Change them; dashboard session only. Takes `profile`, `signals`, `parameters` and, for pointer movement, `acknowledgement_version` |
| `GET /v1/capture-coverage` | Coverage manifest |
| `GET /v1/capture-coverage/friction?hours=` | Friction candidates; dashboard session only |
| `GET /v1/capture-coverage/heatmap?hostname=&pathname=&hours=` | Pages with pointer samples or one page's grid of counts; dashboard session only |
| `POST /v1/capture-depth/delete` | Delete recorded high-fidelity events now; dashboard session only, needs `confirm: true` |
