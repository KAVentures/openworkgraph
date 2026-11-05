# Observed foreign-record interruptions and context returns

The production `linked_work_items` response now annotates each **activity segment** with two additional presentation-only fields:

- `observed_foreign_record_events_before`: number of independently observed, non-tainted foreign-record events strictly between consecutive member observations, within their exact matching capture scope. It is zero for other boundary types and for the first segment.
- `resumed_record_context`: true only when the above interruption exists and both neighboring member anchors name the **same literal record**, are untainted, and have strictly increasing observed instants.

Example: observations A1, B1, C1, A2, B2, A3 in one complete capture scope yield three segments for record A. The latter two expose witnessed foreign-record counts 2 and 1 and mark returns to the same record context. Neither the B nor C observations become members of A.

These are **not task-resumption assertions**, durations, employee-performance measures, or permission to automate. They do not change case membership, candidate acceptance, confidence, consent, provenance or tenant authorization. Other tenants/actors/devices/sessions, time-only gaps, and missing evidence never contribute to a count. No foreign record ID or scope identity is published. Existing `boundary_before` values remain unchanged for REST/MCP/dashboard consumers; new fields are additive.

Acceptance: `pytest -q tests/test_reconstruction_segments.py`, then all six required exact-head CI workflows and native Ubuntu/macOS/Windows, browser, collaboration, scale, accessibility, starter, packaging and release jobs. Synthetic assertions are not evidence of family-held-out F1 improvement or a consenting-user pilot.
