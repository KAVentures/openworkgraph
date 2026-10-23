# Local evidence deletion

The dashboard's **Delete recent evidence** control (last 15 minutes, last hour
or a custom range) calls the authenticated local `POST /v1/evidence/delete`
route. It deletes everything on this computer that overlaps the range:
canonical, normalized and context events, run memory and outcome watches, the
agent brief log, opted-in browser work text, collector outbox rows, JSONL copies
and screenshots. The range is tombstoned so late or re-delivered evidence cannot
recreate it, and unsent rows are marked never to be shared with a Gateway.
Evidence already synchronized to an organization Gateway is not recalled; see
[data lifecycle](DATA_LIFECYCLE.md) for Gateway retention and purge.

## AI conversation messages

The confirmation includes **Also delete AI conversation messages and agent
working detail from this time**, checked by default. The API field is
`include_agent_conversations` and defaults to `false`, so existing API callers
keep their previous behavior.

When included, agent conversation messages and agent working detail observed in
the range are deleted, and the range is recorded in a separate conversation
tombstone. Later imports from Claude Code or Codex session files, including an
explicit re-import, skip messages and working detail from that time. The
AI tools' own session files are never modified. When the option is not chosen,
those records are kept, and later conversation imports for that time are not
blocked.

## Verification and receipt

After deleting, the route re-reads every local time-indexed store using the
deletion's own overlap rules and returns `verification` with per-store counts
of rows still overlapping and `verified_clean`. Stores the deletion did not
cover (agent conversations when the option is off) are counted separately.
`receipt` is a SHA-256 over the range, deleted counts and verification; it
contains no event IDs, titles, URLs or message text. The dashboard shows
whether the deletion verified clean, how many conversation records were
deleted or kept, and the receipt prefix.

Tests: `tests/test_v057_evidence_deletion.py`,
`tests/test_evidence_deletion_receipt.py` and `tests/js/evidence_delete.test.mjs`.
