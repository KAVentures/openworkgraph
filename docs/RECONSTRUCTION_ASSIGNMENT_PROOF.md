# Accepted assignment explanations (Worker C, #326 A / #370 R1–R2)

The existing read-only `linked_work_items` object in workflow evidence now
explains **how an unreferenced observation became a member of an already
evidence-linked item**. This is an inspection feature, not a new source join,
case classifier, task boundary, handoff, authorization or outcome receipt.

## Analyst workflow

1. Open the existing workflow-evidence API response and select a linked item.
2. Check `unreferenced_member_count`,
   `record_anchored_unreferenced_member_count` and
   `unanchored_unreferenced_member_count`. These are exact over the returned
   item's complete membership, not merely the displayed proof subset.
3. Inspect `assignment_proofs` (at most 24) for a loose observation. An
   `anchored_by_accepted_links` entry contains an ordered event-ID path
   **from the loose observation to an independently exact-record-referenced
   event** and the accepted link basis for each hop. These links were already
   admitted by the linker; this feature does not admit any new ones.
4. `no_exact_record_anchor` means no such source-record anchor is connected.
   `path_exceeds_display_hop_limit` and
   `path_outside_visible_event_window` suppress the whole path rather than
   leaking partial chains. The underlying count still includes reachable
   members. `assignment_proofs_truncated` discloses the 24-entry display cap.
5. If `multiple_exact_record_anchors_present` is true, **do not interpret
   the chosen nearest witness as the unique or authoritative business case**.
   Multiple source records can be linked by a witnessed transfer without
   proving one case. Ambiguous candidate links remain unmerged.

## Boundaries and safety

- Paths are shortest accepted-link paths in a deterministic, sorted,
  multi-source breadth-first traversal. At most eight hops are displayed;
  all 50 visible item event IDs constrain disclosed proof chains.
- Source anchor means only a valid independently observed exact resource
  reference, not provider verification or a business outcome.
- Paths carry already-exposed event IDs and accepted basis names only.
  No app, window title, URL, source host, tenant, actor, device, session,
  clipboard token or text is added to the response.
- Existing consent, tenant scope, retention/revocation and permission checks
  occur upstream; explanations are recomputed from the current admitted
  observations, never persisted or used to execute business actions.
- A returned path is an *explanation of the linker's decision*, not
  independent evidence that the decision is correct.
- This feature does **not** claim improvement in the frozen holdout F1 or
  contamination scores. Run `python -m pytest -q
  tests/test_reconstruction_assignment_proof.py
  tests/test_work_item_links.py` and the complete native Ubuntu/macOS/Windows,
  browser, process-collaboration, accessibility, scale, starter and release
  workflows on the exact PR head before merging. Independent consenting-user
  validation is still required for real-world accuracy claims.


## Review unassigned weak candidates (additive API feature)

The same existing authenticated, read-only workflow-evidence response now includes
`linked_work_items.ambiguous_assignment_review_queue`. This is an **actionable
analyst review list**, not a new membership decision. It is derived from the
linker's existing unmerged `candidate_links`, and only lists observations that
are **genuinely singleton unassigned components** and whose weak evidence
points to an already visible, source-record-anchored linked item. A component
omitted by the item-display limit, a non-singleton unanchored component and a
target without an independently observed exact source record are never
silently relabeled as eligible singleton/case targets.

For example, two independently observed A actions and two B actions share
one multiplexed browser tab. A later unreferenced action in that tab remains
unassigned. Instead of silently joining A or B, the review queue exposes
the loose event ID, the already visible candidate item ID, the actual weak
relation `same_tab_multiplexed`, and the number of distinct observed witness
events. An analyst can inspect the source evidence before making a separate
human decision; **this API does not itself persist a decision**.

Each row contains `event_id`, `review_priority` (either
`competing_visible_items` or `weak_evidence_only`),
`candidate_item_count`, `candidate_items`,
`candidate_items_truncated`, `auto_assigned: false` and
`requires_human_review: true`. Each candidate item contains its existing
`item_id`, `weak_relations`, `distinct_witness_event_count` and
`distinct_candidate_edge_count`. Duplicate identical candidate edges count
once. Evidence-volume ordering helps triage; it is **not calibrated confidence,
case probability or approval**. Rows with multiple candidate items are
displayed before single-candidate rows, with deterministic event-ID ties.

The top-level `ambiguous_assignment_review_total` and
`ambiguous_assignment_review_competing_count` are exact over eligible
observations in the currently admitted, validated evidence and **visible**
linked items. The response displays at most 24 review rows and four candidate
items per row; `ambiguous_assignment_review_truncated` and each row's
`candidate_items_truncated` disclose the display bounds. A missing row is
not proof that the observation is correctly assigned or that no work occurred.
Removing an exact-record anchor or revoking retained evidence recomputes the
queue rather than preserving a stale recommendation.

This is backward-compatible with existing `candidate_links`, accepted
memberships, evidence paths, record-case lanes, REST/MCP consumers and the
dashboard. It adds no provider reads, LLM calls, raw text, app names, source
hosts, tenant/person/device/session IDs, clipboard values or new authorization.
It does not infer a handoff, verified business outcome, unique case identity
or human-confirmed assignment.

Run the focused native integration checks:

```bash
python -m pytest -q tests/test_reconstruction_assignment_proof.py tests/test_work_item_links.py
```

Full exact-head native cross-platform, browser, collaboration, accessibility,
scale, starter and packaging/release acceptance and independent consenting-user
validation are still required. This feature improves **reviewability of
unassigned work**, not measured F1 or contamination by itself.


## Review two-hop unassigned work fragments (additive API feature)

The existing read-only workflow-evidence response also exposes
`linked_work_items.ambiguous_fragment_review_queue` for a narrower case
that the direct review queue cannot show: an unassigned observation may be
connected to a **visible, independently source-anchored item** through
exactly **one other still-unassigned singleton observation** using two
already-emitted weak candidate edges. For example:

```
A (exact record) --surface_continuity--> loose X
loose X --surface_continuity--> loose Y
```

Y has no direct visible source-anchored candidate. It can now be presented for
human review with a literal `witness_event_path` of `[Y, X, A]` and two
`weak_relation_path` bases. A competing B route appears as another option,
not as an automatic selection. X remains unassigned too. The system does
**not** merge X/Y with A, infer task identity, or promote candidate edges to
verified evidence.

This projection requires the same **complete** organization, actor, device
and session identity on every hop, plus the same known application and capture
source. It rejects malformed/tainted claims, hidden linked targets, unknown
app/source, cross-tenant or cross-session hops, and linked or non-singleton
intermediaries. Only visible, source-anchored items are eligible. An
observation already having a direct candidate stays in the one-hop queue;
two-hop paths never recursively grow to three or more edges.

Each row has `event_id`, `candidate_item_count`, bounded
`candidate_items`, `candidate_items_truncated`, `evidence_hops: 2`,
`review_priority`, `auto_assigned: false` and
`requires_human_review: true`. Each option includes the existing
`item_id`, `distinct_intermediary_count`,
`distinct_two_hop_path_count`, a literal three-event witness path and two
weak relation bases. The two-hop path is an **explanation of an ambiguous
suggestion**, not an accepted assignment or a calibrated probability.

At most 24 rows and four options per row are displayed, with exact eligible
and competing totals **only when** `ambiguous_fragment_review_scan_complete`
is true. If input exceeds 10,000 candidate edges/singletons or the two-hop
fanout exceeds 20,000 potential paths, the helper fails closed: no
suggestions, `scan_complete: false`, and null totals rather than misleading
zeros. `ambiguous_fragment_review_truncated` is true for a bounded display
or incomplete scan. These are analysis/display budgets, not claims about
source coverage. Removal or revocation of observed anchors recomputes
suggestions from current evidence; no review decision is persisted.

The additive response reuses existing authenticated REST/MCP projections,
with no new permissions, raw text, source host, tab/clipboard token, provider
call, saved decision, agent authorization or business write. Existing
`linked_work_items.items`, direct candidate review, event membership,
capture and dashboard remain unchanged.

Focused regression command:

```bash
python -m pytest -q tests/test_reconstruction_assignment_proof.py tests/test_work_item_links.py
```

The complete exact-head Ubuntu/macOS/Windows pytest, browser,
process-collaboration, scale, accessibility, starter, installation and
packaging/release workflows must pass before merge. No F1/contamination
improvement or real consenting-user validation is claimed from this review
feature.
