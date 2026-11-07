# Observed group continuity (Worker C, #326)

The existing authenticated `/v1/work-resumption` response now includes
`observed_group_continuity` and `observed_group_continuity_truncated`.
Each candidate requires an identical provider-scoped resource pair to have
been **co-observed in one event in both idle-gap sessions**. Independent
resource recurrence (even A and B alternating in both sessions) is not enough.

Each candidate exposes the current review-only group ID, earlier observed
window, bounded matched opaque pointers, and pair count. It explicitly sets
`proves_same_case: false` and `requires_review: true`. This is not an
automatic case merge, confirmed workflow, task assignment or agent authority.
Transitive co-reference is not treated as direct pair corroboration.

The existing saved-history grant controls all scanned events; no new
permissions, network calls, raw text, file paths or titles are introduced.
The response is additive and existing REST/MCP consumers remain compatible.
A scan may be truncated; absence of matches is not proof of no continuation.

## Acceptance

Run `pytest -q tests/test_work_resumption_group_continuity.py` and the
required repository CI. Real consenting-user validation and preregistered
assignment F1 / contamination targets are independent and remain outstanding.
