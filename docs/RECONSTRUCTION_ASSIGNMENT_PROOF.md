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
