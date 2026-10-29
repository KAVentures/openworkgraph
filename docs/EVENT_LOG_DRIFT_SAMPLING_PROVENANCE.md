# Concept-drift window sampling provenance (#326 H4)

Concept drift compares directly-follows profiles over **at most 600 sampled cases per window** (the configured `settings.sample_per_window`), selected deterministically by a hash of each case identifier. The full eligible window size may be larger than the cases actually used for the profile, permutation test and `paths_that_moved` case shares. These statistics describe the sampled cases, not necessarily the entire source population.

The read-only response now includes two additive fields for every `windows[]` entry:

- `cases`: existing full eligible case count (unchanged).
- `sampled_cases`: exact number of distinct cases retained in the `dcase` analysis sample for this window.
- `sampling_truncated`: true when the sample omitted at least one eligible case (`sampled_cases < cases`).

A window with exactly 600 cases is **not** truncated; a window with 601 cases has `cases: 601`, `sampled_cases: 600` and `sampling_truncated: true`. These fields are aggregate counts only and reveal no new case IDs, raw events or activity labels. Sampling, window assignment, thresholds, permutation method, Bonferroni correction and case-share calculations are unchanged. The new fields do not change existing REST/MCP routes or the shape of existing fields; clients that ignore unknown fields continue to work.

The regression compares two synthetic monthly windows at the 600/601-case boundary and confirms that identical directly-follows profiles produce no change point. This is a provenance and disclosure improvement, **not** proof of real-provider completeness, statistical representativeness or customer acceptance. A detected drift can still reflect recording changes rather than a business-process change. Authorization, tenant isolation, consent, retention and action approval are unchanged.
