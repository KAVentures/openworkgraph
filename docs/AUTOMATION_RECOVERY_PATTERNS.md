# R18 — Retained issue sequences and later-completion intelligence

In Automation monitoring, select a retained deployment and choose
**Review issue sequences and later completions**. Enter an explicit UTC
start date and exclusive end date (up to 366 days), and the exact per-run
reported specification SHA-256, or explicitly select unknown attribution.
Unknown attribution may mix versions. The current report of each retained
run is analyzed; this is not historical replay, runtime attestation or
real-time monitoring.

## Four integrated views

- **Issue sequence patterns:** group runs by the exact ordered sequence of
  failed or handed-to-human step IDs and statuses, including repeated
  occurrences. The empty sequence is a separate group, not proof of
  successful execution. Counts distinguish run status and later
  completion from unresolved episodes.
- **Step issue episodes:** for one stable step ID in one run, the first
  failure/handoff opens an episode. Further issues of that ID remain in
  the episode until a later reported completion of the same ID. Other
  steps and skipped occurrences do not close it. A later issue after
  completion opens a new episode. Remaining episodes are separately
  classified for running and terminal runs. A terminal success can
  still contain an unresolved episode. This is report structure, not
  independent retry or recovery verification.
- **Consecutive issue transitions:** adjacent entries in the issue-only
  sequence, even if ordinary steps intervene. Counts distinguish
  occurrences from distinct runs. No causal dependency is inferred.
- **Exact retained runs:** inspect run digest, issue sequence, episode
  boundaries, reported elapsed time and original retained run steps.
  Drill down from a pattern, step or transition; the run filter applies
  to the entire run, not necessarily the selected issue group. Separate
  flags include later same-step completion, unresolved episode and
  reported success with an issue. Flags can overlap.

Elapsed duration is from first reported issue to the later same-ID
completion. The sequence index establishes order; a negative elapsed
timestamp is withheld rather than made positive. The median includes
only completed episodes with available nonnegative duration. There is
no claim that omitted steps did not happen, that a completion was a
retry, that a human handoff was resolved, or that business outcomes
were achieved.

## Exact scope and evidence

The existing local monitoring lock protects the read of runs and ordered
steps. One bounded read includes up to 10,000 runs and 250,000 steps;
oversized populations or more than 20,000 groups refuse rather than
silently sample. The population digest binds exact scope, attached
design metadata and every retained run digest including all step
payloads. Changes in a label, status or timestamp invalidate prior
pages. Later pages and group drilldowns require the digest. The exact
run detail endpoint independently verifies the selected run digest.
An exported JSON page is a snapshot, not a live attestation; refresh
before using it as current evidence. Labels and outcome source text
are not copied into the aggregate/episode report. Stable identifiers
may still be sensitive. The existing exact detail flow exposes
authorized retained step labels when explicitly opened.

API: dashboard-session-only POST
/v1/automation-monitoring/deployments/{deployment_id}/recovery-patterns.
Strict JSON up to 16 KiB, up to 50 rows per page and 2 MiB response.
Example:

    {"reported_spec_sha256":"<exact digest or null>",
     "start_date":"2026-01-01","end_date":"2026-02-01",
     "view":"patterns","offset":0,"limit":25}

Views: patterns, steps, transitions, runs. The runs view supports
run_filter (all, has_issue, no_issue, later_completion,
unresolved_episode, succeeded_with_issue) and at most one exact
pattern_sha256, step_id or transition_sha256. Later pages and focused
requests require expected_population_sha256. No new retained tables,
remote action, execution permission, source calls or provider access.

## Verification

New regressions cover real retained storage, exact ordered sequences,
distinct run and occurrence accounting, stale population pins, exact
run references, unknown attribution, empty cohorts, minimized exports,
timestamp inconsistencies, strict inputs, bounded scans and 160
independent randomized episode partitions. Existing Chromium monitoring
acceptance exercises exact scope, view switching, group drilldown,
original run evidence and edit invalidation. Full native CI remains
required.
