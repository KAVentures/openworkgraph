# Simulation review reports

After a successful what-if run in the event-log workspace, **Download JSON**
exports an `openworkgraph.simulation-report.v1` document with the submitted
scenario, server-resolved scenario (including seed), calibration coverage,
baseline and scenario results, uncertainty when requested, and all assumptions.
The report also records the download preparation time. It is a snapshot of that
completed run: subsequent form edits do not change its contents.

**Download CSV** exports baseline and scenario cycle-time percentiles and mean,
throughput, queue stability, activity resources, durations, utilization and wait
metrics, plus assumptions. Durations have explicit units; null values remain
empty rather than becoming zero. JSON retains the full report, including pool,
calendar, routing and uncertainty details. CSV is a summary, not a replay format.

Both downloads use the browser and send nothing to a Gateway. They include
activity labels, log metadata and declared schedules, which can be sensitive;
review before sharing. Spreadsheet formula prefixes are escaped by the existing
CSV exporter. These are modelled outcomes under stated assumptions, not measured
business savings, verified process improvements or permission to execute.
