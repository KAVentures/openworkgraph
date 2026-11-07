Identity-level interruption bursts
=================================

The existing authenticated and grant-scoped /v1/work-resumption response now includes segments for each per-resource episode and earlier/current pointer track. Each segment groups successive observed events for the same provider-scoped resource separated by at most five minutes, even when unrelated activity keeps the global idle-gap session active. Segments expose observed UTC first/last timestamps, up to six opaque event IDs, and the observed gap from the previous segment. At most the latest 20 segments are returned, with an explicit truncation flag.

These are observation bursts, not proven tasks or case boundaries. Five minutes of resource silence can include unobserved work. No new associations are made, and existing candidates, confidence, history grants, time-only rejection and REST/MCP fields remain compatible. No text, URLs, paths or external sources are read.

Validation: pytest -q tests/test_work_resumption_identity_segments.py tests/test_work_resumption.py. Synthetic tests are not independent holdout F1 or consenting-user acceptance.
