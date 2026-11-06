# R9 numeric event-log projection (foundation only)

`server.event_log_metric_projection.summarize_projected` accepts an already
authorized, bounded cohort of source mappings and an explicit numeric field
allowlist. It discards all non-allowlisted fields before invoking the existing
derived-metric evaluator. Its return value is aggregate-only (count, null,
sum, mean, min, max and definition SHA-256). It never returns raw event rows.

## Required caller controls

This helper **does not authenticate or authorize anything**. Before calling,
the production integration must independently validate a live local dashboard
session, saved-log ownership/tenant scope, current retention and revocation
state, explicit read consent and bounded cohort filters. A field being present
in a source record does not authorize exposing it as a metric. Use a separately
approved numeric-field schema, never arbitrary user-provided attribute names.

The helper refuses malformed allowlists, nonmapping rows, unsupported nested
or boolean values and cohorts exceeding 1,000 rows. Missing numeric values
remain null; division by zero remains null. Numeric strings are permitted for
CSV-imported numeric fields, subject to the evaluator's decimal bounds. Every allowlisted non-null value is checked, including values not visited by a short-circuited expression. Aggregate sums and means use bounded 100-digit decimal precision independent of the caller's ambient decimal context; sums use high-precision accumulation so row permutations and cancellation do not change results; only the final sum is subject to the magnitude bound. The underlying general-purpose derived-metric helper has a separate order-dependent accumulator and must not be substituted for this projection path.

## Verification and limitations

Run `python -m pytest -q tests/test_event_log_metric_projection.py`.
The tests cover numeric summaries, field exclusion, malformed allowlists,
unsupported values, oversize cohorts and zero denominators.

**Not yet a shipped UI or API feature.** Follow-up work must connect the
authenticated saved-event-log reader and dashboard to this adapter, with
tenant isolation, retention/revocation, actual-browser and end-to-end tests.
No provider or real-customer acceptance is claimed. Never infer individual
worker performance, causation or customer outcomes from these aggregates.
