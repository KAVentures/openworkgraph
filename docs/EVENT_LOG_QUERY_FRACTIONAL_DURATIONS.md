# Typed event-log analysis: fractional case durations

The local, read-only typed analysis builder computes its built-in
`duration_seconds` case value as DuckDB `epoch(max(ts) - min(ts))`,
preserving fractional seconds from imported timestamps. Previously
`date_diff('second', min(ts), max(ts))` truncated the elapsed interval
to whole seconds while the existing `time_between` case metric already
retained subsecond precision.

The correction applies to built-in case-duration median/p90/average and
other fixed aggregate functions, and to the existing strict
`duration_over_seconds` and `duration_under_seconds` case filters.
The filters remain positive/nonnegative integer thresholds with **strict**
`>` and `<` comparisons. A 1.500-second case matches `over: 1`;
an exactly 1.000-second case matches neither `over: 1` nor `under: 1`.
No new query syntax, endpoint, parameter, response key or saved-analysis
format is introduced.

A synthetic CSV regression covers 0.5, 1.0, 1.5 and 2.75-second cases,
an equivalent UTC-offset timestamp, both strict filter directions,
percentiles and agreement with the existing `time_between` metric.
This checks numeric consistency on imported local records, **not**
provider-confirmed completeness, actual business outcomes, causation,
individual productivity, or consenting-customer acceptance.

The source's timestamps may be rounded, missing, delayed or inaccurate.
Retaining arithmetic precision does not improve their evidentiary quality.
Existing typed/parameterized SQL boundaries, consent, local storage,
authorization, tenant scoping, revocation/retention, REST/MCP/dashboard
compatibility and non-executing analysis behavior are unchanged.
