# Evidence-backed cross-record bridge review (#326 A / #264 Stage 2)

The existing read-only `linked_work_items` projection, also available through
`list_workflow_evidence_candidates`, now includes an **observed record bridge**
section for every returned linked item. This makes it possible to inspect
*which exact admitted observations* caused two different source-record lanes
to be linked, without guessing from a time gap or exposing the underlying
clipboard token or capture-scope identifiers.

## API fields

Each item adds:

- `observed_record_bridge_count`: exact number of cross-record union-tree
  witness edges used in this returned item (not all possible pairs).
- `observed_directional_bridge_count`: edges with a literal ordered
  copy→paste observation in one verified capture scope.
- `observed_legacy_token_bridge_count`: edges from the older generic-only
  shared-transfer-token capture contract, **without observed direction**.
- `observed_record_bridges`: first 24 bounded witness edges;
  `observed_record_bridges_truncated` indicates omitted edges.

Each edge includes response-local `record_ids` (matching
`observed_record_runs[].record_id`), `witness_event_ids`, UTC
`witness_observed_at`, `evidence_class`, `direction_observed`, and
`capture_scope_verified`. `copy_record_id` and `paste_record_id` are
populated **only** for an actual ordered directional pair; otherwise both
are null. A bridge never exposes raw provider record IDs, tab/transfer tokens,
actor/device/session/tenant values, app labels, titles or clipboard contents.
The existing item-level `resource_refs` field is unchanged for backward
compatibility; consumers must continue to apply their existing authorization
and privacy controls to that field.

## Algorithm behavior

The production linker still groups identical valid source records first.
For an explicit two-record transfer, it now selects an actual source-record
copy→paste witness and joins those two event IDs, rather than calling every
other observation with the same transfer token a direct transfer endpoint.
Same-record evidence independently includes the remaining observations.
A competing different-record copy before the paste, an unreferenced explicit
directional endpoint, reversed/equal timestamps or capture-scope mismatch
prevents that directional join. Generic observations between a unique copy
and paste do not erase their independent evidence.

Previously supported generic-only shared-token links remain supported, but
their bridge is labeled `legacy_shared_token`, not `ordered_copy_paste`.
Reused multi-record tokens still require the existing disjoint fully witnessed
episodes; incomplete, interleaved or overlapping episodes remain unmerged.
Single-record tabs and same-record edges cannot fabricate a cross-record
bridge. The graph is a **spanning set of actual membership-join witnesses**,
not a complete catalog of observed transfers or an authoritative business
record relation.

## Acceptance and limitations

Synthetic regression coverage in
`tests/test_reconstruction_link_proof.py` includes exact copy/paste endpoint
selection, REST/workflow-evidence projection, generic legacy tokens, disjoint
reused transfers, competing copy origins, cross-actor isolation, time-only
rejection, 30-edge truncation, pseudonym consistency and private-value
exclusion. Run:

```sh
python -m pytest -q tests/test_reconstruction_link_proof.py tests/test_work_item_links.py tests/test_reconstruction_record_path.py tests/test_reconstruction_owg_native.py
```

This is an observation-derived, **read-only** diagnostic. It does not
confirm a business handoff, task identity, causal source-system relation,
completed outcome or authorization for an agent to act. There is no new
capture, source/provider request, persistence, model call, grant or business
write. No private holdout truth or preregistered threshold was modified.
The F1 ≥ 0.90 and contamination ≤ 0.05 targets still require an independent
held-out evaluation and native cross-platform CI; synthetic passing tests
alone are insufficient.
