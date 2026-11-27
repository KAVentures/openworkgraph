# Workday RaaS event and population-control feeds

Refs #1572, #326 and #370 R3/R20. This connector consumes approved Workday custom reports exposed through Reports-as-a-Service (RaaS). It supports business event ingestion for customer-defined HR, finance and procurement processes; it is not a general HR export, native audit-log reader or Workday transaction executor.

## Configure the source

A Workday administrator must create two Advanced reports enabled as web services:

1. A flat event report returning one row per immutable event with exact aliases for event ID, case ID, activity code, timestamp and optionally a resource ID. No additional fields or nested objects. Timestamps must be ISO 8601 instants with explicit offset, at most six fractional digits.
2. A separate aggregate control report returning one row with a nonnegative integer count as a string, including a literal zero for an empty window. Independently validate that its business criteria and security population equal the event report's intended population, and that it is not subject to the same accidental report-row truncation.

Both reports must accept required scalar date/time prompts for the exact half-open UTC window (since inclusive, until exclusive). Configure their prompt XML aliases explicitly. Test both JSON URLs in Workday before enabling recurring reads. The exact report author, business filters, timestamp semantics, source completeness and scope must be reviewed by the customer. Counts and repeat reads cannot prove that the configured criteria are correct.

Use a dedicated Integration System User (ISU) restricted to the approved reports and least required domain permissions. This adapter implements HTTPS Basic authentication for tenants which permit that method. OAuth-only tenants are not supported in this slice. No independent principal or privilege readback is claimed. Do not select sensitive employee fields, compensation, clinical or personal records unless separately justified and authorized; activity codes must be approved non-sensitive labels.

Set OWG_WORKDAY_CATALOG to the deployment-owned JSON mapping:

    {"hiring":{"host":"wd2-impl-services1.workday.com","tenant":"fixture","report_owner":"owner","event_report":"OWG_Events","count_report":"OWG_Control_Count","integration_user":"owg_isu","credential_env":"OWG_WORKDAY_PASSWORD_HIRING","since_prompt":"Since","until_prompt":"Until","event_field":"Event_ID","case_field":"Case_ID","activity_field":"Activity","timestamp_field":"Event_At","resource_field":"Resource_ID","count_field":"Total","activity_labels":{"OPEN":"Request opened","APPROVE":"Request approved"}}}

The approved host must match the Workday service-host grammar; there is no arbitrary endpoint, external result link or redirect. The ISU name is configured without its tenant suffix. Set the referenced password in the existing environment or restricted owner-only private-file mechanism. Passwords never enter the dashboard. The source definition and credential are pinned; replacing either requires reviewed reconfiguration.

## Operator workflow

Open Enterprise connectors and choose an approved Workday report pair. Inspect the event report, control report, tenant, exact definition digest, activity labels and resource handling. Configure initial time, incremental window, overlap and reporting lag. Approve both report reads, control-count reads and metadata retention. Saving or refreshing inventory makes no provider request.

A run requests the control count, the event report twice and the control count again, with identical prompt values. Every event row must match the configured flat schema, map to an approved label and lie inside the window; duplicate event identities are rejected. The event population must equal the independent control counts, and the two complete projected event sets must match. Report output ordering is irrelevant. Unknown root fields, redirect responses, malformed control reports, partial results and unexpected fields fail closed.

The four requests share the existing page, byte, time and row budgets. This is intentionally bounded, non-paginated RaaS; reduce the window when limits are exceeded. No missing population is silently accepted. Matching results do not establish transactional snapshot completeness or historical change coverage.

The common retained-feed engine atomically appends events and advances the checkpoint, deduplicating overlapping event IDs. Case, event and resource IDs are profile-scoped HMAC pseudonyms; raw identifiers and passwords are not stored in retained event rows. The existing recurring jobs and governed snapshot publication use the shared provider contract. Native private crosswalks, reviewed in-place rotation and live provider acceptance remain outstanding.

## Tests and limitations

Synthetic HTTP transport tests cover the exact request paths, prompts, Basic credentials, control counts, repeated-read agreement, empty results, reordered rows, rejected redirects, unexpected columns, timestamp validation, replay, secret drift and disabled consent. Actual tenant report serialization, ISU permissions, count semantics, rate limits and performance must be accepted in a real Workday sandbox. Do not mark the roadmap's live-provider acceptance complete based on fixtures.

Official references:

- https://developer.workday.com/documentation/hto1529968349582/ConceptReportsasaServiceRaaS
- https://developer.workday.com/documentation/fdy1570549197818/ConceptAccessingRaaSOutput
- https://doc.workday.com/workday-education/en-us/course-manuals/creating-integrations-using-workday-orchestrate/orchestration-data-access.html
