# H3 map comparison: median-gap sample provenance

The read-only `/v1/event-logs/{log_id}/map-comparison` response
reports full-cohort `median_gap_seconds_first` and
`median_gap_seconds_second` for each returned directly-follows path.
For the most frequent paths that have at least two observed transitions
in each cohort, `median_gap_difference` includes a bootstrap interval.

**These are different denominators when a path has more than 1,000
transitions in a cohort.** The two full-cohort median fields use every
observed transition, whereas `median_gap_difference.seconds` and
`median_gap_difference.ci95` use deterministic reservoir samples of up
to `GAP_SAMPLE` transitions from each cohort. The point estimate is
the sampled second median minus the sampled first median. It need not
equal the difference of the two full-cohort medians.

The nested `median_gap_difference` object now also reports:

- `sampled_gaps_first`, `sampled_gaps_second`: actual sampled
  transition counts used by the interval, not the full path counts.
- `sampled_median_gap_seconds_first`,
  `sampled_median_gap_seconds_second`: sample medians underlying
  `seconds`, so their difference equals the reported point estimate.
- `sampling_truncated_first`, `sampling_truncated_second`: whether
  fewer transitions were sampled than the corresponding full-cohort
  `transitions_in_first` / `transitions_in_second` count.

No new case identifiers, event rows, raw resource labels or source reads
are returned. Existing path frequency, full-cohort medians, bootstrap
method, sample cap, output fields, REST/MCP/dashboard contracts, local
consent, tenant isolation and retention remain unchanged. A
`median_gap_difference` object is absent on paths not selected for
the bounded bootstrap; its absence is **not** evidence of no change.

Cohort overlap still violates the independence assumption used by the
interval; the existing `overlapping_cases` and `note` disclose that
limitation. Neither the interval nor a difference proves causation,
verified business outcomes, worker performance or permission to act.

Synthetic regressions check exact full-support and capped-support
denominators and sample-median consistency. They do not establish
provider-sandbox acceptance, representative sampling, a consenting
customer pilot or competitor parity.
