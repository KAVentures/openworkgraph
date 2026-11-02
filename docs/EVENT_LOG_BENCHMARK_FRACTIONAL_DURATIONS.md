# Cohort benchmark fractional-duration precision

The local event-log cohort benchmark measures a case's elapsed duration as
`epoch(max(ts) - min(ts))` in **seconds**, retaining fractional seconds
instead of truncating to integer-second boundaries with
`date_diff('second', ...)`.

This applies to the per-cohort throughput p50/p90, sampled median-throughput
difference and bootstrap interval, and SLA breach counts/rates. The existing
SLA rule is **duration > sla_seconds** (strictly greater): a 1.000-second case
does not breach a 1-second threshold; a 1.500-second case does. The accepted
SLA threshold remains a positive integer number of seconds. No new endpoint,
permission, export shape, capture, or upload is introduced.

A synthetic CSV regression covers 0.5, 1.0, 1.25, 1.5 and 2.75-second
cases, verifies both cohort percentiles and the strict threshold boundary,
and checks that the sampled median difference uses the same elapsed units.
These are descriptive calculations on imported records, not proof of
source completeness, business outcomes, causation, individual productivity,
or real-provider/customer acceptance. The existing sample-size provenance
and overlap caveat still apply.
