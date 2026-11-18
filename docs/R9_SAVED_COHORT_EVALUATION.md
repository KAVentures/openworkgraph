# R9 saved cohort metric evaluation

The local authenticated dashboard can save a version-1 typed numeric expression under
a specific imported event log. Selecting a saved expression and choosing **Calculate
cohort metric** now evaluates the stored revision by ID rather than rebuilding an
expression from the form controls.

`POST /v1/event-logs/{log_id}/cohort-metric-definitions/{metric_id}/evaluate`
accepts `{"cohort": {...}}` and optional `case_type` query parameter. The response
contains the same bounded aggregate as `/cohort-metric`, plus
`saved_metric: {metric_id, name}`. A live `OWG-Session` dashboard session is
required both before processing the request and before returning results.
AI bearer tokens cannot access this endpoint. The stored expression is loaded
only from the specified log; unknown, deleted, or other-log IDs are rejected.
The workspace lock spans definition lookup and calculation, preventing a
concurrent delete/append from changing the evaluated revision. No raw case IDs
or event rows are returned. Existing cohort parsing, field allowlist, case
count budget, and numeric null/division rules remain in effect.

Changing any metric calculation control clears the saved selection, so an
unsaved edit uses the existing ad hoc endpoint instead. The dashboard's cohort
filters are applied to either path.

Tests: `pytest -q tests/test_event_log_saved_cohort_metrics.py
tests/test_event_log_cohort_metrics.py`. Acceptance still requires full
exact-head CI, authenticated browser testing, and main-branch integration.
This endpoint does not provide tenant sharing or a general SQL language.
