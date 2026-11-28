# Reviewed prospective case exclusions

Refs #1572, #326 and #370. A local operator can exclude explicitly reviewed native cases from future retention in one existing enterprise profile. This is a prospective data-minimization control, not a deletion or erasure workflow.

## Operator workflow

In Enterprise connectors, open **Prospective case exclusions**. Select a supported profile with no unreconciled run or integrity hold. Enter 1–100 exact native identifiers, one per line. For typed sources, enter the native object/table type, a tab, then the exact ID. The resolver uses the same canonical case identity as the adapter: REST, SQL, curated warehouse/report sources, Salesforce, ServiceNow, SAP, Dataverse, HubSpot, Oracle Fusion and selected Zendesk tickets. Google Workspace and Slack are not covered by this resolver.

Review the requested population, new exclusions, already excluded cases, matching retained objects and integrity status. When a retained dataset exists, the structural audit must pass before absence is accepted. If any requested new exclusion already appears in retained data, the entire change is refused. No selective deletion or automatic repair is performed. Coordinate any required whole-dataset deletion, downstream removal and source-side erasure separately.

The preview pins the exact canonical case pseudonyms and private profile state. Apply requires three explicit confirmations and repeats the review within a transaction. A stale plan, changed revision, unreconciled run or existing retained match refuses the entire change. Successful apply stores at most 5,000 profile-scoped case pseudonyms, advances the revision and disables the feed. Separately enable it and reauthorize scheduling/Gateway jobs. Exclusions are additive only; no automatic removal or policy widening. The raw identifiers are transient local inputs, and are not newly persisted by this workflow; existing adapter configuration may already contain native identifiers. The dashboard clears the entered list on success.

## Retention boundary

The normal retained engine filters projected rows before canonical append/import, using the existing immutable admitted profile configuration. It records excluded event counts in the latest run result. A successfully admitted request with all rows excluded may advance the checkpoint without fabricating a dataset. This does not mean the provider omitted those records from the HTTP response: the provider may still return them transiently, and the adapter validates the response before the exclusion filter runs.

New snapshot preparation refuses a selected window containing an excluded case; the integrity audit flags excluded objects found in the retained dataset. This is defense in depth, not a guarantee against arbitrary external workspace writes. Other profiles, old downloads, publications and backups are unaffected. Profile or dataset deletion clears the exclusions; a dataset-deleted profile cannot be reenabled. Recreating a profile creates a different pseudonym namespace and requires new exclusions. Historical reconciliation (#1594) must be integrated against the same retention rule before release. Never infer provider deletion from missing records.

## API

All endpoints require the existing local human dashboard session.

- GET /v1/enterprise-connectors/case-exclusions
- POST /v1/enterprise-connectors/case-exclusions/preview
- POST /v1/enterprise-connectors/case-exclusions/apply

Preview accepts profile_id, revision and cases (native_case_id and optional native_type). Apply additionally accepts plan_sha256 and confirm_exact_exclusions, confirm_prospective_only and confirm_dispatch_reauthorization. Responses contain counts, local profile metadata and a review digest, not raw case IDs or the stored HMAC keys. Local inventory exposes only the exclusion count.

## Acceptance

Regression fixtures cover retained and all-excluded windows, overlap replay, exclusion absence, existing-evidence refusal, exact ID resolution, stale review, transaction rollback and active-run refusal. Focused enterprise CI includes the regression module. Live provider and historical reconciliation acceptance remain outstanding.
