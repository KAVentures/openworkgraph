# Event-level observed pair assignments

The authenticated `/v1/work-resumption` response now includes `observed_pair_event_assignments` and `observed_pair_event_assignments_truncated`.

Each entry is one exact provider-scoped pair observed in the **same event** of the latest idle-gap session. `current_evidence_event_ids` lists up to eight event IDs containing both identities; `earlier_corroborations` lists up to eight earlier sessions containing the same pair, with up to eight matching event IDs per session. Truncation is explicit at each bound. Pair entries are limited to 24. All IDs are from the existing sanitized, grant-scoped pointer query.

A singleton observation of A, or an interleaved event for B, does not belong to the A+file pair. Co-reference is **not** proof of a shared task, case, transfer or completed outcome; transitive links do not imply an assignment. Consumers must not authorize business actions from this derived evidence. Existing group and candidate fields remain compatible.

The response is read-only, follows the existing saved-history AI grant, and does not fetch titles, URLs or content. Evaluate with `pytest tests/test_work_resumption_group_event_assignments.py`; full native CI and consenting-user acceptance are separate gates. This feature does not establish preregistered F1 or contamination thresholds.
