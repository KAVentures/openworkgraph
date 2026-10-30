# Dotted-chart event coverage and omissions (#326 H2)

The local, authenticated read-only dotted-chart endpoint returns at most the requested number of cases and at most the configured number of dots. Case selection is spread across the chosen cohort's ordered cases; selected cases may then have their events thinned for display. Neither mechanism implies that omitted source events are absent.

The existing `coverage.events` counts all events in the selected cohort, while `coverage.points_shown` counts actual returned dots. Additive counts distinguish the two omission stages:

- `coverage.events_in_shown_cases`: all original events belonging to the case rows selected for display, before point thinning.
- `coverage.events_omitted_by_case_sampling`: `coverage.events - coverage.events_in_shown_cases`; events belonging to case rows not selected.
- `coverage.events_omitted_by_point_thinning`: `coverage.events_in_shown_cases - coverage.points_shown`; events in selected rows that were not returned as dots.

These counts are exact for the selected cohort, including empty cohorts. They satisfy `coverage.events = coverage.points_shown + coverage.events_omitted_by_case_sampling + coverage.events_omitted_by_point_thinning`. The existing `cases_sampled` and `points_thinned` booleans retain their meanings. The activity palette still comes from the entire retained log, as before.

This is counts-only disclosure: it does not alter the sample, thinning algorithm, case identifiers, timestamps, source events, consent, authorization, tenant isolation, retention, REST route, or existing response fields. It does not establish representative sampling, complete capture, verified business outcomes, or customer/provider acceptance. The regression uses a synthetic 50-case/150-event CSV fixture and checks full, case-sampled and case-sampled-plus-thinned results.
