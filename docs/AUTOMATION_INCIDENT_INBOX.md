# Reviewing automation incidents

In Automation monitoring, select a deployment and open **Show incidents**.
The scope selector switches between that deployment and all local deployments.
Use the status selector and literal search to narrow the current snapshot by
deployment name, incident kind or step. Counts for open, acknowledged and
resolved incidents cover the chosen scope before filters; the displayed count
reflects the filters. Refresh retrieves a new snapshot. Changes to the selected
deployment discard an in-flight result from the previous selection.

Acknowledge, resolve and reopen retain the existing checks against both the
last observed report and review revision. They record a local review; they do
not retry an automation, grant an action, or verify a business outcome.
Incident names and notes may contain business information and stay within the
existing authenticated local monitoring view.
