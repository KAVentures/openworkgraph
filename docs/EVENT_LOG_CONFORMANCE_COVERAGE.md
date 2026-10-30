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

The same response additionally reports **distinct observed variant counts**
without running a second query: `max_variants_applied` (the bounded
requested cap), `variants_selected` (number of ranked variants retrieved,
including too-long ones), `variants_aligned` (selected variants actually
aligned), and `variants_skipped_trace_too_long` (selected variants above
300 events). These satisfy `variants_selected = variants_aligned +
variants_skipped_trace_too_long`. They count **distinct paths**, not
cases; one variant can represent many cases. `variants_selected` may
equal the cap without proving that further variants exist, while the
separate case-omission count and `variant_limit_truncated` identify
actual unselected cases.

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

## Distinct-variant denominator (additive)

The same ranked query also reports `variants_observed`: the exact number of
distinct observed paths **within the selected case notion**, before the
`max_variants` cap. `variants_not_selected_variant_limit` is the difference
between that count and `variants_selected`; it excludes the selected variants
that are too long to align. The count uses `count(*) OVER ()` on the already
grouped path relation, not a second source query. If the selected case relation
is empty, both counts are zero. This gives two separate partitions:

```text
variants_observed = variants_selected + variants_not_selected_variant_limit
variants_selected = variants_aligned + variants_skipped_trace_too_long
```

Unlike case counts, these values measure distinct traces, not the number of
people, events or cases. They do not establish source completeness, causality,
provider acceptance, or fitness of omitted variants. Synthetic tests exercise
frequency ties, truncated variants, too-long selected traces, an empty selected
relation and the authenticated HTTP response; no real-provider/customer
acceptance is claimed.
