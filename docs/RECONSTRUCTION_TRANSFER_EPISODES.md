# Disjoint observed clipboard episodes under a reused token

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326)
and [#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264).
Implementation: `server/reconstruction_transfer_episodes.py` integrated into
`server/work_item_links.py`; read-only `linked_work_items` in the existing
workflow-evidence API.

## Functional behavior

A browser/capture tool may reuse the same opaque clipboard-transfer identifier
for multiple distinct operations. Previously, one exact-scoped token observed
on **three or more distinct records** was rejected in full to prevent
interleaved cases being fused. This remains the default. When the evidence is
stronger, OWG now recovers *independent observed episodes* instead of discarding
all evidence.

For example, a single token in the same exact organization/actor/device/session
with the ordered events `A copy → B paste → C copy → D paste` produces **two**
linked work items, `{A,B}` and `{C,D}`, rather than one four-record item or
four unrelated observations. The recovered link basis is the existing
`clipboard_transfer`; its existing conservative confidence and
non-authoritative status are unchanged. Earlier/later exact same-record
observations can join their corresponding item independently.

This is intentionally **not** a generic copy/paste sessionizer. Recovery
requires all of the following for a multi-record token group:

- An already validated literal source-record reference and **all four present,
  valid capture-scope components** (organization, actor, device, session)
  on **every** observation; no loose event or missing legacy scope can be
  silently skipped. Existing legacy two-record behavior is unchanged.
- Exactly consecutive explicit `copy`, then `paste` observations, with
  strictly increasing validated UTC instants and different record identities.
- Each record identity occurs in **only one** episode, preventing a transitive
  bridge through a shared record. A maximum of 64 complete episodes is allowed;
  a larger group is withheld in full.
- No conflicting duplicate IDs, malformed reference/scope/action claims,
  contradictory top-level/metadata token IDs, or tainted token.

Interleaved `A copy → C copy → B paste → D paste`, overlapping
`A copy → B paste → B copy → C paste`, missing paste, generic action, unknown
record and timestamp ties remain **unmerged** by this token. Other valid
same-record links are unaffected. The existing two-record token behavior
(including legacy nondirectional capture) is unchanged. Timestamp order
can reject an inconsistent directional claim, but time alone never creates
a link. Tokens are isolated by exact organization, actor, device and session.

## Read-only response

The existing `linked_work_items` response adds two aggregate counters:

```json
{
  "recovered_reused_clipboard_transfer_groups": 1,
  "recovered_disjoint_clipboard_transfer_episodes": 2,
  "link_contract": {
    "reused_clipboard_token_only_links_disjoint_explicit_episodes": true
  }
}
```

The example omits existing fields. Counters describe admitted observations
in the current bounded response input; they are **not** business transaction
counts, task completions, handoffs or verified workflow relationships.
No raw clipboard token, content, window title, customer data or additional
tenant/actor/device/session identifier is exposed by these new fields.
No new persistence, provider call, capture grant, MCP operation, execution
authority or business write is introduced. Existing response fields remain
backward compatible.

## Verification and remaining limits

Run:

```bash
python -m pytest -q tests/test_reconstruction_transfer_episodes.py tests/test_work_item_links.py tests/test_reconstruction_segments.py tests/test_reconstruction_switches.py tests/test_reconstruction_surfaces.py
```

Synthetic tests cover independent episodes, reversed input order, exact-ref
continuity, interleaved/overlapping/partial/malformed claims, scope isolation,
oversized groups, privacy and the actual workflow-evidence API projection.
Native Ubuntu/macOS/Windows pytest, browser, accessibility, process
collaboration, scale, starter, packaging/release, reviews and current-main
compatibility remain required for PR merge.

This feature improves deterministic evidence recovery for a narrow class of
reused identifiers. It is **not** proof of higher preregistered family-holdout
assignment F1, contamination ≤0.05, real consenting-user task boundaries or
verified business outcomes. Private truth, splits, scoring and thresholds
are unchanged; a held-out evaluation must be reported separately.
