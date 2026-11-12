# Cross-idea benefit evidence review (roadmap R16)

OWG now offers a **read-only, local** comparison of after-period benefit measurements for different implemented automation opportunities. This helps reviewers find potential double-counting when considering a benefit portfolio. It does **not** sum savings, prove that interventions caused any change, or fetch source data.

## Use

Open the local **Automation opportunities** panel and select **Review overlapping benefit evidence**. The same report is available to an authenticated local dashboard session at `GET /v1/automation-pipeline/benefit-overlap`. Normal AI bearer tokens, untrusted browser origins and remote callers cannot use this local dashboard-only route.

The report checks already recorded `after` measurements associated with implemented ideas. For each pair, it requires both:

1. The source **kind** and nonempty user-entered `source_reference` to match **exactly**.
2. Calendar date intervals to intersect, inclusive of boundary dates.

It never merges sources by title, topic, timestamp alone or semantic similarity. A matching source/date pair is only a **review candidate**: two automations could have used the same source report to measure disjoint case cohorts. Conversely, different references might point to the same cases. Review the underlying scope and identity before claiming or combining value.

The report exposes only idea/measurement IDs, source kind, temporary source group labels and overlapping dates. It does not expose raw source-reference values, customer names, case records or receipts. The existing per-idea report remains authoritative for its own period coverage. Internal source-group labels are transient and cannot be used as provider IDs.

## Limits and interpretation

The scan is bounded by the local portfolio cap (1,000 ideas × 48 measurements) and returns at most 100 matching pairs. Truncation, unreadable stored evidence and missing source references are explicitly disclosed. A zero-candidate result **never certifies** that benefits are independent: sources may be unknown, incompletely linked, stale or semantically related under distinct references.

No automation is blocked or approved by this review. It does not change existing assessments, measurements or portfolio states; it cannot authorize execution or attest to realized savings. Consent and local-only defaults remain unchanged.
