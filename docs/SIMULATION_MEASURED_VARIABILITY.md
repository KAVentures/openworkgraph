# R14 — measured service-time variability

Opt in to `distribution: "measured_gamma"` in simulation or temporal validation. A Gamma distribution matches the coefficient of variation of **selected event-to-event gaps**. The selected minutes (median, mean or user override) determine its mean; use mean basis for moment matching. Gaps include waiting and are not measured processing times.

Both baseline and scenario use the same dispersion calibration. Portfolio comparisons may select the mode on the baseline or individual alternatives. Reports disclose each fallback: fewer than ten gaps, CV outside 0.01–10, or constant gaps. No individual gaps are retained or returned. Validation measures dispersion from pre-cutoff selected cases only.

Existing fixed and exponential defaults remain unchanged. Gamma mode cannot be combined with the exponential-based estimation-uncertainty replications. Shared seeds reproduce runs, but changes in routing or scheduling may change which service draws are assigned to a case. Simulation is not observed savings, a forecast, or execution authorization.

Regression tests cover sampled moments, M/G/1 queueing theory, identical scenarios, training-only calibration, fallback and portfolio comparison. Native CI and real service-time validation remain separate gates.
