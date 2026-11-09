# Focus and switching

**Export › Focus and switching** shows how recorded foreground time on this computer was split
between apps: the uninterrupted blocks of time in one app, and how often you switched.

## How blocks are formed

Recorded focus spans are taken in order:

- Spans in the same app with gaps of at most two minutes join one **focus block**.
- A **short detour** to another app (under 30 seconds) followed by a return to the same app
  is folded into the block, and is neither a switch nor a block of its own.
- A move to another app within the gap is a **switch**.
- After a longer gap, a new block starts without counting a switch.

A block's length is the elapsed time from its first to its last span, short detours
included.

## The report

- **Per local day**, in the time zone you choose: recorded time, focus blocks, switches,
  switches per active hour (for days with at least ten minutes recorded), and the median and
  longest block.
- **Overall**: median block, blocks of 25 minutes or more, the app pairs switched between
  most, and the ten longest blocks with their short detours.

A report covers up to 92 days. Gap and detour thresholds can be changed through the API.

Switching is not a measure of attention, productivity or quality of work; many jobs need
frequent switching. The report reads only this computer's local history.

## API (dashboard session only)

`GET /v1/focus-report?start=YYYY-MM-DD&end=YYYY-MM-DD&tz=Europe/Stockholm[&max_gap_seconds=120][&ignore_detours_seconds=30]`
