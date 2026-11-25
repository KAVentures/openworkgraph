# Temporal simulation sensitivity (R14)

The optional simulation validation panel compares 2–4 explicitly selected
holdout shares and 1–2 seeds using one unchanged simulation configuration.
Each run calibrates on the earlier eligible cases through the existing
validation implementation. Results expose every split/seed, case counts,
relative errors, and whether the declared absolute relative-error tolerance
is met for median, p90 and mean. Undefined errors and stopped routes are
unresolved rather than counted as within tolerance.

POST /v1/event-logs/{id}/simulation/temporal-sensitivity uses the existing
dashboard-only authorization and returns no-store JSON. The request accepts
holdout_shares, seeds, cases (50–2000), distribution, activity_time_basis,
routing, and max_absolute_relative_error (0–10). The projected-event cap is
100,000 and at most eight simulations are performed. The local workspace
lock holds across the review to prevent concurrent local source changes.
This can temporarily delay other local workspace operations; use small logs.

Cutoffs may repeat and held-out populations overlap. This is descriptive
sensitivity, not independent cross-validation or forecast assurance.
No model or seed is selected using the holdouts, no causal or completion
claim is made, and no execution or source-system action occurs. Existing
single-cutoff validation remains unchanged. Full CI and browser acceptance
remain required.

Each split also includes separate observed duration, simulated range and
within-split seed spread per metric. Spread is withheld with fewer than
two resolved seeds or any unresolved seed. Observed cutoff, case counts
and duration summaries must agree across seeds or the review refuses.
The dashboard exposes the number of simulated cases per run, validates
distinct shares/seeds before submission, and displays seed sensitivity
separately from changes across observed periods.
