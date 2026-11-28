# Guided follow-up reassessment

Extends the reviewed reassessment workflow (#1631). A Gateway owner can use two existing approved request files rather than manually construct a comparison JSON document.

1. Export the baseline conformance request and later reviewed linkage request from the linked case review console.
2. Open a completed-unverified follow-up packet and choose Guided comparison. Upload the two request files and explicitly approve inspection.
3. Review eligible source candidates. Select distinct corresponding sources, then explicitly select matching later linked cases with compatible source multiplicity and evidence-basis category.
4. Review the original packet rules and map each required activity. Labeled activities must match; coded selectors can be selected from observed later activities or explicitly entered when absent. A candidate is not an independently verified identity match.
5. Approve the exact correspondence, build the comparison, inspect outcomes and separately approve recording and downloading the report.

The backend recomputes the original pinned conformance, validates the subsequent linkage digest and current grants, filters eligible sources by provider/disclosure mode/window, and returns bounded ephemeral group and activity catalogs. The canonical builder reconstructs rules without permitting changed rule types or thresholds and calls the existing transaction-bound reassessment engine. The final recording rechecks exact revisions, report digest, grants and correspondence constraints. No mapping is persisted by discovery; recording retains the existing digest-only receipt.

The manual comparison API remains available. The new owner-only routes are POST /v1/admin/connector-events/{organization_id}/exception-queues/{queue_id}/followups/{packet_id}/reassessment-catalog and /reassessment-build. The latter requires the exact catalog digest and explicitly chosen source, case and selector mappings.

This still requires independent human review, distinct live publications and two approved input files. It does not automatically match identities, perform corrective actions, prove source completeness or establish causation. The report measures observed sample outcomes only.

Regression fixtures compare guided and manual canonical reports and exercise stale catalog, wrong mapping, approval and window guards. Dashboard JavaScript syntax is checked; runtime acceptance and live provider validation remain outstanding.
