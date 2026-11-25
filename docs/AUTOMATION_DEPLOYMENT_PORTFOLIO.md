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

## Version rollout evidence review

Select **Review version rollout evidence**. Choose distinct retained baseline
and candidate deployments, and inspect or enter their exact reported workflow
specification SHA-256 values. The same common inclusive start and exclusive end
window is applied to each deployment. Enter minimum attributed-run count,
minimum candidate success share, maximum allowed candidate decrease, and
maximum unhashed share. All thresholds are explicit. No workflow is deployed,
approved, contacted or rolled back by this review.

The report partitions each complete retained window into exact selected
version, other version and unhashed runs. Within each partition it shows
succeeded, failed, handed-to-human, cancelled and running statuses. The
attributed eventual reported-success bounds are succeeded / attributed and
(succeeded + running) / attributed. For candidate-minus-baseline subtract
opposite interval endpoints. A bound is unavailable for an empty denominator.
The separate unhashed sensitivity assumes any subset of unknown-attribution
runs might belong to the selected version; unknown known-success reports are
excluded for the lower endpoint, unknown known-non-success reports for the
upper endpoint. Running reports can have either outcome. It does not change
actual attribution or pass the declared gates. No random sampling or causal
inference is assumed. Distinct retained cohorts may have different case mixes
and runtimes. A gate pass is not independent evidence of production safety.

Inspect exact paginated run evidence for any cohort, attribution or status.
The population digest binds both deployment metadata, exact review basis,
all run rows and all step rows in the common window. Later pages refuse a
changed population; changing evidence filters does not change the review
population. A detail link rechecks the exact individual run hash using the
existing run evidence API. Download JSON explicitly; it includes the reviewed
summary and the current evidence page, not all run bodies. Imported labels
are rendered as text. The local dashboard session is required for all reads;
no source account, network call, or new persistent tables are introduced.

POST /v1/automation-monitoring/rollout-review (strict JSON, 16 KiB):
`baseline` and `candidate` each contain deployment_id and spec_sha256;
`started_from`, `started_before` are timezone-qualified timestamps;
`minimum_attributed_runs`, `minimum_candidate_success_share`,
`maximum_success_share_decrease`, `maximum_unknown_attribution_share` are
explicit thresholds (shares are fractions). Optional evidence_side,
evidence_attribution, evidence_status, offset, limit and
expected_population_sha256 select evidence pages. Limits: 366 days,
50,000 retained runs, 2 million steps and 100 evidence rows per page.
Regression tests cover real retained report imports, unknown attribution,
finite bounds, exact pinned detail, stale population, limits and HTTP access.
Full native and browser acceptance remain necessary.
