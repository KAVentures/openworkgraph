# Cross-deployment portfolio evidence (R18)

The local automation monitoring dashboard now includes **Review all deployment
evidence**. Choose a recent interval of 1–365 days and review every retained
deployment in one view, without contacting a runtime or external provider.

The overview combines the latest operator release declaration, the deployment's
reported workflow specification, the attached reviewed design, and the exact
per-run reported specification claims. It shows missing or changed declaration
pins, unmatched design/specification hashes, missing artifact digests for pilot
or production declarations, old/missing reports, and version attribution gaps.
Select a deployment to inspect its existing release review and history.

## Interpretation

- Run counts are partitioned into claims matching the current reported spec,
  claims for another spec, and runs with no comparable spec. The same
  mutually exclusive partition is reported for the selected recent interval.
- The recent interval includes reported run-start timestamps between the
  snapshot cutoff and snapshot time; future starts are not counted as recent.
  A missing or old run is not proof of downtime or failed delivery.
- One bounded grouped query over the local monitoring database provides the
  portfolio evidence snapshot. It does not join external incident/channel
  stores into a misleading supposedly atomic snapshot.
- No listed evidence gaps does not mean a release is ready, deployed, safe,
  successful, or business-outcome verified. A hash is not artifact attestation.
- The view is dashboard-session-only and no-store. It returns no run bodies,
  individual run IDs, step content, secrets or reviewer notes. No remote
  command or execution grant is issued. Deleting a deployment removes it from
  subsequent views. Refresh after changing a release or importing reports.

## Verification

Run `pytest tests/test_automation_deployment_portfolio.py` together with the
existing monitoring and release inventory suites. The existing real-browser
monitoring scenario checks the portfolio, window selection and release
drill-down. Full CI and real-provider acceptance remain necessary.
Related: [#370 R18](https://github.com/KAVentures/openworkgraph/issues/370).
