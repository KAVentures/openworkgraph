# Conditional retained-run concurrency bounds (R18)

In Automation monitoring, open the retained run concurrency review, choose the
completed UTC window, exact specification (or unknown attribution), and declared
capacity threshold. Select **Conditional concurrency uncertainty** as the evidence
view. The original measured views remain available.

For each selected retained record with a positive terminal interval, the model
fixes its clipped reported start and end. For records classified as unresolved
by the existing review (running or without a terminal timestamp), the model
allows any completion between the clipped start and the end of the window.
The lower trajectory assigns zero duration to these unresolved records; the
upper trajectory extends all of them to the window end. Neither is an imputed
actual runtime history. Zero-length or boundary-only terminal intervals are
excluded from both trajectories.

The exact sweep reports lower and upper peak concurrency, lower and upper
run-microseconds, and three mutually exclusive classes of elapsed time:
above capacity in the measured trajectory, additionally possibly above capacity,
and not above capacity anywhere in the envelope. Above means strictly greater
than the declared threshold. The two extremal completions simultaneously
achieve these bounds under the model. The dashboard lists paginated half-open
segments with lower and upper counts and the threshold classification.
Large integrated run-microsecond totals are decimal strings.

The calculation is conditional on the selected retained population and reported
timestamps. Records not selected by the original interval query (including
reports with a finish before the window), unknown unretained runs, overwritten
history and missing provider evidence are not bounded. An upper-envelope value
is not verified actual concurrency, capacity, resource utilization or a staffing
recommendation. It never authorizes or contacts a runtime.

The existing exact review pin covers the interval records and settings, including
the selected view; changed settings or records invalidate pagination. The
existing dashboard-session requirement, scope and no-store response remain.
The implementation is stacked on #1651 to preserve its existing CI; do not
merge into the parent branch. Integrate the parent into main first, then
reconcile this change and validate the combined tree.

## Acceptance

Run `python -m pytest -q tests/test_run_concurrency_bounds.py tests/test_automation_run_concurrency_bounds.py`
and the existing retained concurrency/browser monitoring tests. The core suite
exhaustively enumerates hypothetical completions for 250 randomized tiny
populations, checks boundaries and malformed inputs, and verifies exact totals
for 10,000 unresolved records over 366 days. Retained-store and HTTP tests
exercise incomplete evidence, paging, pin invalidation, authorization and
preservation of the measured views.
