# R19 — Prospective calibration and frozen case evidence

Open a saved event log and expand Prospective calibration and frozen case
evidence in Predict open cases. Load and select a saved prediction snapshot.
Choose five, ten or twenty fixed equal-width probability bins, a minimum
of 5–5,000 cases per displayed group/bin, and a frozen grouping dimension
(last activity, estimator basis, or no grouping). Review. This never changes
the frozen snapshot, live predictions, or the underlying log.

## Quantile calibration with ties and censoring

For every valid frozen P10, P50 and P90, compare the eventual recorded
remaining time T with the estimate q. The empirical quantile condition
for target tau is P(T < q) <= tau <= P(T <= q). Equality at a point mass
is not necessarily a failure. Compute separate strict and inclusive
membership bounds. A finished case gives known membership. A still-open
case with elapsed time >= q cannot be strictly below; only elapsed > q
rules out inclusive membership. Missing and inconsistent observations do
not establish a completion. A nonnegative eventual time cannot be strictly
below zero. Missing/invalid quantiles are separately counted.

If the lower bound of strict membership exceeds tau, estimates are
definitely too high in the declared finite population. If the upper
bound of inclusive membership is below tau, they are definitely too low.
When all memberships are known and the target lies in the bracket, report
the empirical bracket. Otherwise the direction is unresolved. These are
marginal identification bounds, not confidence intervals or guarantees.

## Outcome reliability and fixed-bin ECE

For snapshots with an outcome, compare model and historical base-rate
probabilities on exactly the same valid paired cases. Each forecaster
uses its own fixed bins, lower-inclusive and upper-exclusive except the
final upper endpoint (1). Count known binary outcomes only for finished
cases. No label is imputed for open, missing or inconsistent cases.
With n cases, k observed positives, u unknown labels and probability
sum S in a bin, possible eventual positive counts are the integers
k,...,k+u. Observed rate lies between k/n and (k+u)/n. Absolute gap
bounds are min/max of |j-S|/n over those integers. The minimum uses the
nearest feasible integer (not an unattainable fractional count).
The maximum is attained at one endpoint.

Sum bin absolute gap numerators, then divide by the paired population
size to obtain sharp marginal fixed-bin expected calibration error
(ECE) bounds for each forecaster, conditional on eventual binary
labels and observation assumptions. Every nonempty bin contributes
even if its separate mean/rate/gap is below the support threshold.
Each forecaster's bounds are individually sharp; subtracting them
does not give a sharp paired difference because bins can differ and
the unknown labels are shared. ECE depends on chosen bins and is not
a proper scoring rule. Use the existing Brier and paired outcome bounds
alongside it. No future quality, statistical significance, or calibration
guarantee is established.

## Grouping and evidence

Group by frozen metadata, never a later activity. Up to 30 supported
groups are shown, with omitted group and case counts. All cases still
contribute to the overall summary. Counts and labels are not formal
anonymization. The minimum is a display-support threshold, not a
statistical sample-size guarantee. The exact local evidence explorer
allows state, visible group and arm/bin filtering and paging; authorized
case IDs and frozen estimates appear only in an explicitly opened page.
Exports are explicit JSON downloads. No new retained tables.

Dashboard-only POST:
 /v1/event-logs/{log_id}/prediction-snapshots/{snapshot_id}/calibration

    {"settings":{"bins":10,"minimum_cases":5,"group_by":"activity"}}

Append /evidence and include the same settings plus evidence:
 expected_review_sha256, optional group_sha256, optional arm and bin_index
 together, state (all, finished, still_open, missing, inconsistent),
 offset and limit (1–50). The digest binds exact frozen snapshot row,
 calibration settings, relevant current case observations and selected
 horizon. A changed basis refuses the next evidence page. A digest is
 not a signature, complete-source attestation, or verification that
 historical backfills cannot occur. The current report is a point-in-time
 observation. All requests are bounded and dashboard-session-only, with
 no-store responses and one concurrent local calibration review.

## Verification

The new regressions include 160 exhaustive binary-label assignment oracle populations and 120 independent quantile-membership oracle populations, tie and boundary cases, discrete ECE minima, subgroup support, actual retained-log append and deletion, stale pins, strict input validation, dashboard authorization and real-browser evidence/download workflows. Native CI remains required.
