# Conformance coverage: ranked variants versus long traces

The read-only `/v1/event-logs/{log_id}/conformance` result aligns only the
most frequent **distinct observed paths**, up to the requested `max_variants`
(clamped to the existing server limit). Even among those selected paths,
a trace longer than `MAX_ALIGNED_TRACE_LENGTH` (300 events) is not aligned.
The returned `fitness` and `fitting_share` are calculated **only over
`aligned_cases`**, not necessarily all `cases` in the imported log.

Two additive counts make the excluded cases distinguishable:

- `cases_not_aligned_trace_too_long` (existing): cases belonging to
  selected variants whose traces exceed the alignment length limit.
- `cases_not_aligned_variant_limit` (new): cases in variants not selected
  because of the ranked `max_variants` limit.
- `variant_limit_truncated` (new): true exactly when the second count
  is nonzero.

For any selected case notion, these are disjoint and satisfy:

```text
cases = aligned_cases
      + cases_not_aligned_trace_too_long
      + cases_not_aligned_variant_limit
```

A false truncation flag does **not** establish that the imported event log
is complete: source systems may contain events not exported to this log.
Nor does a high fitness score prove a verified business workflow. The
reference model remains user-supplied, and the same source event ordering,
alignment algorithm, reference paths, privacy/consent boundaries, and
REST/MCP/dashboard fields remain unchanged. The new fields return counts
and a boolean, never additional case identifiers or raw source rows.

Synthetic tests cover a frequent selected variant with two rarer omitted
variants, the same data with a larger limit, and a 301-event frequent
trace that is excluded by length while a rarer short variant is omitted
by the ranked limit. The tests do not establish real-provider/customer
acceptance, completeness, or competitor parity.
