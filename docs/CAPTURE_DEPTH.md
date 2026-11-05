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

Capture depth only decides what is **recorded locally**. Retention, sharing with your
organization and sharing with an AI or cloud service keep their own separate
settings: work text, screenshots, AI access, MCP tools, Gateway sync and exports.
Changing capture depth never changes them.

## Signals

**Hover dwell** (`browser_control_hover_dwell`)
- Recorded for buttons, links, menu items, tabs, options and similar controls only.
- Each event has the control's safe label and role, the duration rounded to 50 ms,
  and how the hover ended: the pointer left, the control was activated, or the page
  was hidden.
- Not recorded for quick passes, disabled controls, password or other secure fields,
  or touch input.
- The same control is not recorded twice within two seconds.

**Target labels.** Hover and drag events use author-set structural labels only:
`aria-label`, `title`, `placeholder`, and a `<label for>` that holds no editable content.
No element text is ever used, including native buttons and text referenced by
`aria-labelledby`, because a button, row or draggable can hold the user's own content
(a message, a task, a draft). An editable element, or anything inside one, gets no label
and is marked `editable`. The server strips labels from input, textarea, select and
secure targets of these events at ingest.

**Drag and drop** (`browser_drag_start`, `browser_drag_drop`, `browser_drag_end`)
- Records the source and destination controls and the declared payload *kinds*
  (`files`, `text`, `html`, `uri`, `other`).
- Never records the dragged data, text, HTML, file names or file contents.
- A drag that ends without a drop is `incomplete`, and a drop from outside the page is
  `external`.

**Pointer movement** (`browser_pointer_trajectory`, Full fidelity only)
- Samples at the chosen rate, as fractions of the viewport (0–1, three decimals) with
  relative milliseconds and a coarse viewport size.
- Sent in batches of at most 200 points or 10 seconds.
- No samples are taken while the pointer is over a password, one-time-code, card or
  other secure field, or inside a secure widget. The current batch ends at the field,
  and sampling resumes when the pointer leaves it.
- The raw points are kept on this device in their own table
  (`pointer_trajectory_points`), apart from the event rows, and are deleted with their
  event. The stored event carries only a point count and duration
  (`raw_points_stored_locally: true`, `raw_points_shared: false`). So no read path
  returns raw coordinates: not the workflow trace, the events API, MCP (Redacted or
  Full), workflow-evidence bundles in either representation, the history export, or
  JSON, XLSX and CSV-zip exports with or without `redact_names`. Those output boundaries
  also summarize any trajectory that still carries points.

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

## Desktop

The desktop recorder reads the same capture-depth choice, at most every two
seconds, so a change needs no restart.

- **Drag and drop** (`screen_drag`): a mouse press and release at least 16 pixels and
  150 ms apart. It records the start and end points and, when UI labels are on, the
  accessibility targets under both. The ordinary click is still recorded. Dragged
  data and file names are never read.
- **Pointer movement** (`screen_pointer_trajectory`, Full fidelity only):
  - samples are taken at the chosen rate, in screen pixels relative to the primary
    display (negative on monitors left of or above it);
  - batches hold at most 200 points or 10 seconds;
  - no sample is taken while the accessibility element under the pointer is a secure
    field, and the batch ends there. The check runs whenever a sample would be taken and
    the pointer has moved more than 8 pixels. If the check fails, the field is treated
    as secure;
  - no screenshot is taken for these events;
  - the points are stored apart from the event, as in the browser, so shared views
    and exports see only a count and duration.
- **Desktop hover dwell** (`screen_hover_dwell`, Enhanced):
  - A rest is the pointer staying within 3 pixels for the chosen threshold. It ends when
    the pointer moves away (`pointer_left`) or a button is pressed (`activated`).
  - It counts only when the accessibility element under the pointer is a non-secure
    interactive control: button, link, menu item, tab, checkbox, radio, combo box,
    slider, switch or option.
  - Rests over text, empty space, secure fields or apps without accessibility targets
    are dropped. With UI labels turned off, no desktop hover is recorded.

The server's `/v1/events` ingest drops desktop hovers, drags and pointer samples the
current capture depth does not allow. It also drops malformed samples, hovers without a
non-secure target, and events from excluded apps.

## Enforcement

The local server enforces capture depth at ingest, so a stale or modified extension
cannot store a disabled signal. It also:

- refuses high-fidelity events with malformed or oversized metadata;
- stores no high-fidelity events on pages you have excluded;
- falls back to Standard in the extension when the local server cannot be reached.

## Coverage manifest

`GET /v1/capture-coverage` lists each capture modality as `on`, `off`, `unsupported`
or not seen, with counts of high-fidelity events over the last seven days. It covers
browser structural events, hover dwell, drag and drop, pointer movement, desktop
clicks, desktop hover, drag and pointer, screenshots and work text. Reconstruction and agents can use it to tell a missing signal from an absent
behaviour.

## Not yet done

- Desktop hover, drag and pointer movement are covered by unit tests with a simulated
  mouse listener and simulated accessibility targets only. They have not yet been
  regression-tested on real macOS or Windows machines, including permissions,
  multi-monitor layouts and lock states.
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
