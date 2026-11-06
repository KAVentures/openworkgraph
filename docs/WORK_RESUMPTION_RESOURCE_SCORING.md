# Resource-local assignment review (Worker C, #326)

The work-resumption API retains its legacy session-overlap `association_status` for compatibility, but now emits a separate `case_assignment_status` and `correlated_resource_pair_count`. These are **not** proof of task identity.

- `corroborated_candidate`: two non-routine shared resource identities were co-observed on one event in each session.
- `unresolved_independent_resources`: matching resource identities recur but no qualifying co-observed pair recurs; A/B interleaving is not promoted to one case.
- `ambiguous_routine_context`: shared pointers are routine, not sufficient for case assignment.

No content, title, URL or provider data is read; all observations come from the existing sanitized, lease-scoped event scan. Existing session confidence is unchanged for REST/MCP compatibility; consumers making task-assignment decisions should use the conservative new status and ask for review. This does not merge cases or authorize actions. Evaluation against independent family holdout and consenting pilots remains required.
