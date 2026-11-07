# Observed resource case groups (Worker C, #326)

The existing `/v1/work-resumption` response now includes `observed_case_groups` for the current idle-gap session. Each group is a connected component of opaque provider-scoped resource identities that appeared together in at least one **single observed event**. Resources that merely occur near one another in time remain separate groups.

A reviewer can inspect `resource_pointers`, bounded `evidence_event_ids`, `resource_count`, and `assignment_status` (`observed_co_reference` or `independent_resource`). `proves_same_case` is always false: even transitive same-event linkage is not proof of one task. Results cap at 24 groups and 12 event IDs per group, with truncation indicators. The output does not write canonical case assignments, change permissions, or infer from content, time, or unobserved activity.

This is an incremental functional reconstruction feature, not proof of the preregistered assignment F1 >= 0.90 or contamination <= 0.05. Those thresholds and a consenting-user pilot remain outstanding. The current-session scope is the existing saved-history grant; no new source reads or external calls are introduced.
