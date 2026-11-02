# Tabless desktop-app focus: evidence-backed return to an existing case

Roadmap: [#326 Section A](https://github.com/KAVentures/openworkgraph/issues/326),
[#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264),
[#370 R1/R2](https://github.com/KAVentures/openworkgraph/issues/370).

Implementation: `server/reconstruction_app_focus.py` integrated into the existing
`server.work_item_links.linked_work_items` read-only workflow-evidence response.
No new endpoint, recorder, data collection, storage, permission, provider call
or business write.

## What a reviewer can now inspect

Desktop apps do not necessarily have browser tab IDs. The previous linker
exposed adjacent same-app observations as ambiguous candidates only, even
when two independently observed exact-record anchors identified a bounded
return to the same case. The new engine can recover the *unreferenced*
tabless steps between two exact-record anchors, without merging another case.

Example: all rows have the same **complete** organization, actor, device and
capture session identity; UTC instants are strictly ordered:

```text
e01  Terminal  desktop  exact record A
e02  Terminal  desktop  no record reference
e03  Browser   browser_extension  exact record B
e04  Terminal  desktop  no record reference
e05  Terminal  desktop  exact record A
```

The existing linked item for A includes e01, e02, e04 and e05 with
`same_app_cross_app_bracketed_record` (confidence 0.58); B is **not**
joined. Without e05, neither loose step is assigned. If B is in Terminal
instead of a different known app, the bracket is refused. A simpler
`A -> loose Terminal step -> A` with no cross-app interruption receives
`same_app_bracketed_record` (confidence 0.64).

The existing read-only workflow-evidence API adds
`observed_bracketed_app_focus_links` (total recovered membership edges),
`observed_cross_app_bracketed_app_focus_links` (subset containing a witnessed
different-app record), and
`ambiguous_bracketed_app_focus_attachments_suppressed` (post-transfer
component/competing-target vetoes). Each item reports the actual
`link_basis`, event IDs and confidence through the existing response.
Counts are descriptive, not precision estimates. No raw app names,
scope identifiers, source labels, titles, URLs or clipboard contents are
returned by the new feature.

## Strict admission and abstention

- Two valid exact-record A anchors must agree on the literal record identity,
  provider/kind, known application, known source, and all four nonempty
  capture-scope identifiers. Neither anchor may claim a browser tab, clipboard
  transfer, malformed action or tainted identity.
- At most eight unreferenced tabless steps may intervene. Each must have the
  same app/source/scope and no record, tab, transfer, copy/paste, malformed
  or contradictory claim. An elapsed time gap never supplies positive evidence.
- At most sixteen different-app observations may interrupt the bracket. They
  must name independently evidenced exact records in the same complete scope,
  with a different known app, known source and distinct pre-tab record-link
  component. They are *negative interruption evidence*, never positive
  membership or a handoff.
- Same-app competing records, unknown/ambiguous app or source, malformed
  scope, unrelated unreferenced activity, timestamp ties, or excessive spans
  stop the bracket. A fully identified **different** capture scope is ignored
  rather than influencing another tenant's case assignment.
- Before joining, the caller checks the frozen post-transfer component for
  competing records and competing tab/focus targets. No transitive clipboard
  or tab association may override an independent conflicting case.

The resulting membership is still **inferred observed focus**, not a verified
task, workflow boundary, source-authoritative business case, employee
performance measure, handoff or authorization. Missing capture can produce
false attribution despite these gates. Consent, retention, revocation,
tenant filtering and authorization remain enforced by the existing
workflow-evidence read path; no cached membership survives evidence removal.

## Acceptance

Run `python -m pytest -q tests/test_reconstruction_app_focus.py
tests/test_work_item_links.py tests/test_reconstruction_tab_focus.py` in the
native repository. The new integration tests exercise the actual
`workflow_evidence.list_workflow_evidence_candidates` API and positive
cross-app returns, no-return abstention, same-app B conflicts, capture-scope
and tenant isolation, unknown/malformed/clipboard evidence, timestamp ties,
bounded spans, privacy minimization and removal/recomputation.

The entire exact-head required Ubuntu/macOS/Windows pytest, browser,
process-collaboration, scale, accessibility, starter and packaging/release
matrix must pass, with resolved reviews and current-main compatibility,
before merge. The preregistered `evals/reconstruction` family holdout must
report assignment F1, cross-workflow contamination and interruption rejection
without changing the truth or thresholds. **F1 >= 0.90 and contamination
<= 0.05 are not established** by synthetic tests, and real consenting-user
acceptance remains separate.
