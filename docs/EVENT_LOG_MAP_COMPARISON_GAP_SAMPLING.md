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

The reservoir is applied **after** restricting to the specific
directly-follows path in each cohort. Sampling all transitions first
and then filtering by path can underfill a reservoir even when that
path has at least `GAP_SAMPLE` transitions. The per-path selection
uses a materialized filtered relation to prevent that behavior.

The nested `median_gap_difference` object now also reports:

- `sampled_gaps_first`, `sampled_gaps_second`: actual sampled
  transition counts used by the interval, not the full path counts.
- `sampled_median_gap_seconds_first`,
  `sampled_median_gap_seconds_second`: sample medians underlying
  `seconds`, so their difference equals the reported point estimate.
- `sampling_truncated_first`, `sampling_truncated_second`: whether
  fewer transitions were sampled than the corresponding full-cohort
  `transitions_in_first` / `transitions_in_second` count.

The bounded timed-path selection ranks eligible paths by the sum of
observed transitions in both cohorts, descending. When counts tie, it
breaks ties by the literal `(from, to)` activity names, ascending. Thus
exactly 25 of 26 equally frequent eligible paths receive an interval in
a reproducible order, independent of SQL group-row order. Paths outside
this cap still have full-cohort medians but no bootstrap interval;
absence of an interval is not evidence of no change. This ordering
makes no claim about path importance or business causation.

The additive per-path `median_gap_interval_status` makes interval absence
machine-readable for **every returned path**:

- `estimated`: the path has more than one transition in **both**
  cohorts and was selected for a bounded bootstrap interval;
- `insufficient_support`: either cohort has zero or one observed
  transition for the path, so no bootstrap interval is attempted;
- `rank_limit`: both cohorts have at least two transitions, but the
  path falls below the top-25 interval budget (with deterministic
  literal-name tie-breaking).

The top-level `timed_paths_eligible`, `timed_paths_evaluated`, and
`timed_paths_omitted_rank_limit` are aggregate **distinct-path**
counts across the entire comparison, not merely the paths returned
under the caller's `limit`. Their relationship is
`eligible = evaluated + omitted_rank_limit`. They do not count
transitions, cases, or business-verified process variants. An
`insufficient_support` status is descriptive, not a significance test
or proof of no process change. No existing interval is recomputed
or promoted.

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
