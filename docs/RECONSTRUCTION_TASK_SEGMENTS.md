# Exact-record activity segments (Worker C / #326)

This local read-only feature turns already-observed exact opaque resource references into
**reviewable activity segments**. It addresses interrupted and interleaved work
(A → B → A) without combining unrelated business records. A segment is an
observational window, **not** an authoritative task boundary, completed case,
handoff or source-verified business outcome.

Run from the repository root:

```bash
python -m server.reconstruction_task_segments --events /path/to/consented-events.json --consent-local-review --idle-seconds 120
python -m pytest -q tests/test_reconstruction_task_segments.py
```

Input is a bounded JSON list of existing OWG event objects containing event ID,
timezone-aware observed timestamp, organization ID and the existing
`metadata.resource_reference` opaque reference/provider/kind/host. Do not
feed unconsented exports. No provider calls, raw text export, disk writes or
canonical case updates occur. The output contains only opaque reference IDs,
UTC times, normalized action tokens, counts and explicit uncertainty flags.

Each exact scoped resource gets its own chronological lane. A gap greater than
the configured threshold starts a new segment; cross-session observations
remain on the same exact-record lane, but are not automatically joined to a
verified task. Other records appearing between observations mark interleaving
without creating a cross-record link. Conflicting provider/host/organization
identity suppresses the entire reference; duplicate event IDs are excluded.
Missing/invalid timestamps are omitted. Truncation is explicit.

**Limitations:** The threshold is an operator-supplied heuristic, not learned
from a validated holdout. The module cannot infer semantic links between
different records, recognize unseen work, prove task completion or measure
assignment F1. Real consenting-user evaluation and the preregistered family
holdout remain necessary. It intentionally does not change REST/MCP or dashboard
contracts. This module is a CLI integration with existing canonical event
schema and exact-record identity validation, not a new automatic agent action.
