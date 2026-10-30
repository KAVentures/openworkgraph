# Cohort benchmark: sampled throughput interval provenance

The read-only `/v1/event-logs/{log_id}/benchmark` result includes
`cohorts[*].throughput_seconds.p50/p90` computed from **all** selected
case durations. The `differences_second_minus_first.median_throughput_seconds`
point estimate and 95% percentile-bootstrap interval instead use a
deterministic reservoir sample of up to 5,000 cases **per cohort**.
These are different denominators for larger cohorts; the sampled
difference need not equal the difference between the two full-cohort p50s.

The existing `difference`, `ci95` and `method` values are unchanged.
The nested interval object now also includes:

- `sampled_cases_first` and `sampled_cases_second`: actual case-duration
  observations used by the bootstrap, not the 5,000-case maximum.
- `sampled_median_throughput_seconds_first` and
  `sampled_median_throughput_seconds_second`: medians of the actual
  samples. Their second-minus-first difference equals `difference`.
- `sampling_truncated_first` and `sampling_truncated_second`: true when
  the sampled observation count is less than that cohort's full case count.

The existing `cohorts[*].cases` and full-cohort throughput percentiles
retain their meaning. No case IDs, event details, resource labels, raw
rows or new source reads are exposed. Consent, local storage, tenant
isolation, retention, authorization, REST/MCP/dashboard contracts and
non-executing analysis remain unchanged.

The interval assumes independent cohorts. If the same cases occur on
both sides, the existing `overlapping_cases` and `note` warn that
the interval overstates certainty. This is descriptive association,
not a causal explanation, business outcome, staff productivity score
or permission to act.

Synthetic tests cover complete-support and cap-truncated cohorts,
actual denominators, point-estimate consistency and deterministic
repeatability. They do **not** establish provider-sandbox validation,
representative sampling, a consenting customer pilot or competitor parity.
