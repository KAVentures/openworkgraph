# Dotted-chart case sampling includes ordered endpoints (#326 H2)

The authenticated, read-only dotted-chart API sorts retained cases by the selected `order` (`start` or `duration`, with deterministic tie-breaking) before limiting the displayed rows to `max_cases`.

When at least two rows are shown from a larger cohort, the deterministic, evenly spaced selection now **includes both rank zero and the last ranked case**. Previously, sampling ranks with `floor(i * total_cases / shown)` could omit the last case (for example, 50 cases displayed as 10 rows selected ranks 0, 5, …, 45 instead of including rank 49). That concealed the latest-starting or shortest-duration end of the ordered cohort.

For a single displayed row, rank zero is retained; for full coverage, every case is returned in the original sort order. The case/event coverage counts and omissions introduced in #476 continue to describe the actual selected rows and dots. No new response keys, data retention, provider access, permissions, or executable actions are introduced.

Including both endpoints does **not** make this a statistically representative sample, guarantee preservation of rare exceptions in omitted cases, or establish complete observation or verified outcomes. The regression uses a synthetic 50-case CSV log with start and duration ordering, 1/2/10-row limits, and the full-coverage boundary; it is not real-provider or customer acceptance.
