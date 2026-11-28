# R14 — Multi-SLA capacity and inverse deadline review

In **Activity → Large event logs → What-if simulation → Multi-SLA capacity
and inverse deadline review**, select the exact retained source log. Enter
or import the existing bounded capacity-grid inputs, add up to eight named
service targets and up to six inverse completion shares, then prepare and
inspect the complete request before running. The starter assumptions are
illustrative. Set real scenario assumptions yourself; an imported report
does not select its original source automatically.

This extends the capacity grid, not the underlying simulation model.
All original grid limits, routing and service-time assumptions, resource
checks, shared seeds, admission guard and optional cost review still apply.
Each grid cell and seed is simulated exactly once. The existing per-run
completion samples are sorted once for the additional service targets;
no separate calibrations, random streams or simulations are needed.
Raw samples are not returned or stored by the extension.

## Service targets

For N post-warm-up arrivals, C completed cases, A abandoned cases and
U=N-C-A unresolved cases, let K be the ceiling of the declared required
share times N. The ceiling uses exact rational arithmetic derived from
the entered decimal share. For a deadline d, let H count completions
whose simulated elapsed duration is at most d (inclusive). The possible
on-time share is bounded by H/N and (H+U)/N. Known late completions and
abandoned arrivals never count as successes for this deadline.

A run meets the target if H>=K and it is not overloaded. It is a known
miss if H+U<K and not overloaded; otherwise it is indeterminate.
Overload takes precedence as a nonqualifying status. The report exposes
integer counts, lower/upper shares, minimum additional on-time completions
needed, and whether resolving every unknown favorably could reach the
target. The cell reports the minimum lower and upper shares across
replications (worst-seed bounds) and qualifies only if every run meets
the target. A joint envelope qualifies a tested design/demand cell only
if every declared target qualifies in every run. Highest tested passing
rate is not a continuous capacity estimate; nonmonotone patterns remain
visible.

## Inverse deadline bounds

For a target completion share, find the minimum deadline at which that
share is reached over **all arrivals**, not only finished cases. Sort
known completion durations x1<=...<=xC. K is the same exact required count.
The most favorable completion of unresolved arrivals gives a lower
deadline endpoint: zero if K<=U, otherwise x(K-U) if that rank exists,
otherwise no finite endpoint. The conservative upper endpoint is xK
if K<=C, otherwise no finite guaranteed endpoint. Abandoned arrivals
are never assigned a completion. Unknown durations are not imputed;
the lower endpoint permits zero solely as a mathematical possibility.
These endpoints describe the information retained by the finite
simulation, not forecasts or confidence intervals.

Across all seeds, use the maximum of the seed-specific minimum
deadlines for a target share. If even one seed lacks a finite endpoint,
the corresponding all-seed endpoint is null. The report separately
flags overloaded seeds; even a finite deadline cannot qualify an
overloaded design. Seconds are retained in JSON and minutes are shown
in the UI.

## Pairwise decision support

For every pair of designs at the same tested demand and seed, calculate
right-minus-left share bounds using opposite endpoints, exactly from
integer counts. Summarize the minimum lower and maximum upper endpoints
across seeds separately for each target. Stable pairs demonstrate
right weak dominance only if every lower difference is nonnegative
and at least one lower difference is strictly positive; left dominance
uses the symmetric upper-bound criterion. Otherwise report exact equality,
unresolved/tradeoff or overload. Dominance concerns declared service
targets only; resource cost or business value is not part of this relation.
Shared seeds are not matched real cases. No automatic design selection.

## API and export

Use the existing dashboard-session-only capacity-grid POST endpoint:

    /v1/event-logs/{log_id}/simulation/capacity-grid

Add the following optional object to a normal grid request:

    "service_review": {
      "targets": [
        {"label":"Standard","cycle_time_minutes":240,"minimum_completion_share":0.9},
        {"label":"Expedited","cycle_time_minutes":120,"minimum_completion_share":0.8}
      ],
      "inverse_completion_shares":[0.5,0.9,0.95]
    }

Limits: 1–8 distinct printable labels, deadlines 0.001–525600 minutes,
shares 0.01–1, 1–6 distinct inverse shares. Strict schema, no extra
fields, and the existing 64 KiB request limit. Bad service inputs are
refused before calibration. Without this optional object the prior
grid behavior and report schema remain unchanged.

The dashboard exports the complete JSON grid report, including inputs,
calibration fingerprint and additional service review. CSV contains
seed-level target counts, required thresholds and bounds. Imported JSON
is read locally and never run automatically; explicit source selection
and exact reviewed input are required. No new persisted model, collection,
execution grant, provider call or staffing change.

## Verification

New tests cover exact share thresholds, unknown/abandoned accounting,
120 independent enumerations of possible unresolved durations and
finite/infinite inverse bounds, joint envelopes, pairwise dominance,
one simulation per original grid run, invalid-input admission and
authenticated HTTP. Browser acceptance covers preparation, invalidation,
simulation, all-arrival deadline display and JSON/CSV downloads. Native
CI remains required. These are hypothetical finite simulations based on
recorded activity gaps that may include waiting; no observed SLA,
production guarantee, or causal savings is verified.
