# Enterprise source onboarding and configuration drift

Refs #1572, #326 and #370. The local onboarding workbench checks deployment-owned source catalogs before operators create profiles, and compares existing profile pins against current definitions. It complements (not replaces) source-host worker preflight, retained ingestion history, reviewed tuning and live-provider acceptance.

## Operator workflow

Open Enterprise connector operations and select **Source onboarding and configuration drift**. Inspect the local provider coverage table and source-level diagnostics. Filter by provider, locally available sources without profiles, blocked credentials, definition drift or shared credential references.

- An absent or empty catalog is distinct from an invalid catalog. Invalid catalog population is unknown, not zero.
- Locally available means the configured credential or approved local SQL file passed local format/path/identity validation. No source database is opened, OAuth token acquired, service principal independently verified, or provider endpoint contacted.
- Existing profiles are checked against the exact canonical definition digest and local credential binding. A missing source, changed definition or changed credential requires reviewed operator action; this workbench does not mutate or reauthorize profiles.
- Shared logical credential references are flagged for catalog sources. A shared reference may be deliberate; it is not proof of shared actual privileges, nor does the report compare secret values.
- The REST policy section distinguishes allowlisted endpoints from explicitly approved endpoint/credential pairs and counts locally available pairs. A missing explicit pair policy is not proof of pair approval.
- The managed secret-file section counts mapped references without exposing their names, paths or values. Unreferenced mappings may belong to workers or other services; never delete them based on these counts alone. A mapped private file is authoritative; a missing file does not fall back to an old environment secret.

Catalog-based coverage includes SQL, SAP, Dataverse, HubSpot, Databricks, Snowflake, NetSuite, Workday, Oracle Fusion and BigQuery. The remaining shared retained providers are classified separately by fixed endpoint policy, stored OAuth grant or reviewed token profile. Their existing profile bindings are checked; the report does not enumerate every possible unused OAuth grant or Slack token.

## Support export

Download a fresh **counts-only support report** from the onboarding panel. This is a separate server projection, not a client-side redaction of the operator detail. It omits source aliases, definition digests, local profile IDs, endpoint URLs, hostnames, tenant identifiers, source labels, credential references, filenames and values. It includes provider-level counts, bounded diagnostic codes and local policy summaries. Review it before sharing; deployment composition itself can still be sensitive.

## Local API

GET /v1/enterprise-connectors/onboarding returns the detailed local operator projection. Add support_only=true to obtain the counts-only support projection. Both require the existing human dashboard session. Neither authorizes dispatch or modifies profile configuration. Inspection can initialize the normal local workspace schema, and observes current process environment and local files; it is not an immutable multi-host snapshot.

## Acceptance and boundaries

Tests use actual local catalog parsers, retained profile state, key-format checks and private-file semantics; guard HTTP requests and SQL connections; and cover malformed settings, absent sources, definition drift, credential drift, reference reuse, unpaired endpoints and support redaction. The focused enterprise stack CI includes the regression suite. Live service access, source schema correctness, permissions, source completeness and operational performance still require customer-specific acceptance. This is not a secret manager, credential rotation operation, live network probe or automatic onboarding tool.
