# Compare simulation reports

In **What-if simulation**, select **Compare saved simulation reports** to load
2–4 previously downloaded OWG simulation JSON reports from your computer. The
view shows each report's simulated baseline and scenario median, 90th percentile,
mean, throughput and unstable-queue warning. Expand each report to review its log
name and ID, case notion, calibration coverage, simulated case count, assumptions
and recorded scenario inputs. A report can contain up to 2 MiB; invalid formats,
nonfinite metrics and invalid coverage are rejected rather than guessed.

Files are read only in the browser and are never uploaded or saved by this tool.
Changing the selection clears results immediately; a delayed previous file read
cannot restore an old result. The comparison CSV contains the displayed metrics,
units, scope identifiers and the unverified-scope warning on each row, with spreadsheet formula prefixes escaped. Full
inputs and assumptions remain in the original JSON reports; they are not in the
summary CSV. Report names, log names and model inputs can be sensitive; review
before sharing either format.

A shared log ID, case notion or count does not establish equal event content,
filters, source completeness, calibration or stochastic conditions. Inspect
these manually. Results are simulated under their declared assumptions, not
observed savings, verified ROI, forecasts or permission to act. This feature
never runs a simulation, reads a provider, authorizes an agent or changes a saved
process. Uncertainty details remain in the original reports.
