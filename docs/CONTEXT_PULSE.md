# Context Pulse

Context Pulse is the incremental memory surface of OpenWorkGraph's compact Context MCP.
It complements, rather than replaces, `get_workflow_trace`.

## Purpose

A connected AI often does not need the entire retained workflow history on every turn.
`get_context_pulse` answers two narrower factual questions:

1. What canonical evidence arrived since this caller last checked?
2. Which evidence-backed long-horizon findings are new or materially changed?

The AI remains responsible for interpretation and suggestions. OpenWorkGraph does not
turn a repeated pattern into a recommendation, policy, permission, or productivity score.

## Cursor contract

The first call establishes a baseline and returns `next_cursor`. The caller should pass
that cursor back unchanged on its next check. The cursor is caller-owned and is not a
server-side subscription or timer.

Recent evidence is bookmarked by the monotonic local database arrival ID rather than by
`observed_at`. This means an event that arrives late with an older observation timestamp
is still returned on the next pulse.

If one pulse needs multiple pages, OpenWorkGraph freezes the event watermark and finding
snapshot until both are drained. Events that arrive during paging wait for the next
completed pulse, so they are neither mixed into the current snapshot nor skipped.

The encoded cursor contains no captured titles, names, surfaces, or other observed text.
It contains watermarks plus opaque finding IDs/versions needed to know what has already
been delivered.

## Findings v1

The initial factual finding set is intentionally conservative and uses the factual context
timeline, not inferred task labels:

- **Surface engagement**: engaged/foreground seconds, span count and active-day count for
  a surface over the rolling lookback. A change becomes material after another active day
  or another 15 minutes of engaged time.
- **Repeated surface transition**: a directional transition between two different work
  surfaces observed at least three times within sessions. Each additional occurrence is
  material.

Every emitted finding carries evidence event IDs and explicitly reports that it is a
factual aggregate, that task inference was not used, and that it is not advice.

The first call labels findings `baseline`. Later calls emit only `new` or `changed`
findings. If `finding_limit` is smaller than the number of changed findings, unseen
findings are not marked delivered; subsequent calls continue the same frozen snapshot.

## Privacy and disclosure

Context Pulse does not add new capture. It uses evidence OpenWorkGraph already stores.
Typed text and clipboard contents remain uncaptured.

The existing Context disclosure boundary applies to the entire Pulse response:

- **Redacted** remains the default AI representation.
- **Full** is available only when the user selected it and organization policy permits it.
- Organization policy can restrict disclosure.

Canonical local evidence is never rewritten by the Pulse or by AI redaction.

## Pull, not push

Context Pulse is a protocol capability, not an autonomous background sender. An MCP client
calls it when that client chooses to refresh context. Clients that support schedules,
loops, or long-running agent logic can call it periodically; ordinary conversational
clients can call it at session start or whenever fresh workflow context is relevant.

This keeps the core primitive portable across ChatGPT, Claude, Codex, other MCP clients,
and future local or Gateway-based integrations without requiring any one client's
scheduling model.
