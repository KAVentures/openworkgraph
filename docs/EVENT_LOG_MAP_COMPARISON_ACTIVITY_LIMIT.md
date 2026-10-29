# Side-by-side process-map activity limit (#326 H3)

Each cohort's `maps[].activities` list is ordered by descending observed event count, then activity label, and includes at most 64 activities. `maps[].activities_truncated` is **true only if more than 64 distinct activity labels exist in that cohort**. Exactly 64 fills the list but does not omit any label. The implementation queries one additional ranked activity to distinguish these cases without returning source rows or unbounded lists.

The existing `maps[].transitions` and `maps[].start_activities` remain restricted to the displayed activity set. The correction does not change frequency, median-gap, cohort, case-count, comparison-interval, source, authorization, consent, tenant, retention, or REST/MCP/dashboard fields. Truncation is independent for each side of a comparison; it is not a statement that the source log or provider export is complete.

A synthetic DuckDB regression compares two disjoint cohorts with exactly 64 versus 65 activity names, confirming both the disclosure flag and unchanged displayed edges. It does not establish real-provider completeness, competitor parity, or customer acceptance.
