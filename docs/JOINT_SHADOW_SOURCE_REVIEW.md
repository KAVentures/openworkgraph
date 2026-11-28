# Joint native shadow and retained source-state review (P0 #1663)

This feature connects the frozen native test roster (#1680), native trace
review (#1672/#1674) and retained project source-check cohort (#1676). It
reuses both reviewers, not a new inference engine, provider adapter or
outcome store.

1. Prepare and independently retain the reviewed skill package and its
   frozen shadow roster before testing. Keep the roster SHA-256 separately.
2. Run the planned cases in a separately authorized and restricted test
   client. Retain the original native trace JSON and trace SHA-256.
   Complete the existing roster assignment worklist, leaving missing roots
   explicitly null.
3. In the linked project source-check cohort panel, declare the same case
   IDs, select one confirmed terminal operation per case and provide the
   original retained source receipt where available. Include every planned
   case, including missing documents.
4. Expand **Compare native shadow and source-state evidence for the frozen
   cohort**. Select the original roster, trace and assignment JSON files and
   enter the independently retained roster and trace pins. Click **Review
   native and source evidence together**.
5. Inspect the two separate statuses for each case. Download the minimized
   review and retain the original inputs and source declaration for
   reproduction. Any change to files, pins, source case declarations or the
   selected project invalidates the displayed report.

The server requires exactly matching declared case IDs (maximum 25, the
existing source-cohort limit). It recomputes the original native trace
review and existing retained source-check reconciliation, not supplied
review receipts. Native missing/unassigned roots, coverage gaps and
deviations remain distinct from historical source operations verified,
not established, or missing. A separate local-reference flag indicates
whether the source receipt needs review. The report retains the original
plan, package, trace, assignments, source declaration, project
specification and both review digests.

The input limit is 24 MiB including the request envelope; original trace
JSON is capped at 8 MiB, roster and assignment JSON at 256 KiB each, and
the source cohort at 4 MiB. Duplicate JSON keys are refused in the
uploaded roster/trace/assignment documents and request envelope. The
existing retained source checker verifies exact receipt bytes. No new
provider read, model invocation or persistent review record is made.

Matching case IDs are a customer declaration, not proof that the same
case, skill package or project was actually used. Clean native telemetry
and historical source state do not establish that the agent caused an
operation, succeeded at the business task, followed permissions or
invoked a specific skill. The review does not provide an atomic snapshot
across stores, prove prospective enrollment, verify current source state,
grade business outcomes or authorize deployment. Keep the separate
independent outcome assessment and company-controlled rollout decision.

## Validation

```sh
python -m pytest -q tests/test_native_shadow_trace_review.py \
  tests/test_project_source_cohort.py tests/test_joint_shadow_source_review.py \
  tests/test_joint_shadow_source_routes.py
```

The focused tests exercise the real native roster/trace and source-cohort
review logic, including 20 combinations of native and historical source
statuses, mismatched populations, altered receipt bytes, stale pins and
a real FastAPI route with substituted local session and retained-source
boundaries. Full repository, browser and live client acceptance remain
necessary.
