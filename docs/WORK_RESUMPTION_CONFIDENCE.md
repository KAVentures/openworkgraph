# Resource-local resumption confidence

Worker C reconstruction milestone for #326/#264. The existing `/v1/work-resumption` endpoint previously ranked a session with two independently recurring case resources as `likely_resumed` at 0.8 because its session-wide pointer Jaccard overlap was 1.0. This conflated resource recurrence with evidence that those resources belong to one case.

The candidate algorithm now uses observations of the *same resource pair in both sessions* to permit the higher confidence tier. Without that corroboration, one recurring nonroutine resource remains `possible` (0.5), and multiple independently recurring resources remain `possible` (0.55), even when session-wide overlap is 1.0. Routine-only overlap remains `ambiguous` (0.3). Candidate ordering follows this conservative confidence. The unchanged `pointer_overlap` field still reports raw session-wide overlap for transparency.

Co-observation is only structural evidence, not verified business case membership. All candidates remain review-only, `case_assignment_proven=false`, and no task or case is automatically merged. Existing saved-history lease restrictions, time windows, pointer sanitation, REST fields, and MCP consumers are unchanged.

Acceptance: independent A/B interleaving is not promoted to likely resumed; a pair co-observed in both sessions retains stronger status; one resource and access-off behavior remain conservative. Run `pytest -q tests/test_work_resumption_confidence.py tests/test_work_resumption_resource_scoring.py`. This milestone does not change the `evals/reconstruction` work-item linker, establish assignment F1 >= 0.90, contamination <= 0.05, or substitute for consenting-user pilots.
