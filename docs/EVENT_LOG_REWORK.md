# Rework and loops

**Rework and loops** in the event-log workspace shows which activities happen again in the
same case, what loops they form, and how cases with rework compare with cases without.

- **Per activity**: how many cases repeat it, the share of the cases containing it, and the
  extra occurrences.
- **Loop kinds**: cases with an immediate repeat (A, A), a short loop with one step in
  between (A, B, A), or a longer return to an activity.
- **Loop patterns**: the most common loops of up to six steps (for example
  *Check → Correct → Check*), with how many cases show each. A case counts once per
  pattern.
- **Comparison**: median throughput time of cases with rework against cases without, with a
  bootstrap 95% interval for the difference (when both groups have at least five cases), and
  mean events per case.

Rework is associated with longer cases here. That does not show the rework caused the
delay: complex cases may both loop and take longer. The analysis reads at most 200,000
cases; use a cohort for larger logs. It respects cohorts, case types and applied activity
groupings.

## API (dashboard session only)

`POST /v1/event-logs/{log_id}/rework` with `{"limit": 20, "cohort": {...}, "case_type": "..."}`.
All fields are optional.
