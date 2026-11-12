# R16 — Paired process value measurements (first delivery)

This opt-in **human-only** organization process feature adds an append-only,
revisioned before/after comparison to the existing shared process catalog.
It deliberately does **not** mark a benefit as realized, authenticated, causal,
statistically significant or customer-verified. It does not call providers or
agents and never grants execution privileges.

## Data and review

1. Create an explicitly shared process and obtain independent review of its
   exact current revision.
2. Prepare two disjoint calendar windows (baseline and intervention), each with
   a bounded **case count**, **aggregate total handling minutes**, and 1–20
   **SHA-256 fingerprints** referencing source receipts. Do not include source
   record bodies, customer names, raw identifiers, emails or patient records.
   The fingerprints are caller supplied; they are not authenticated evidence.
3. Explicitly confirm sharing the aggregate comparison with the organization.
   Save against the exact current process revision, its content SHA-256, and
   current access revision. Every change creates an immutable new revision.
4. An authorized human can reopen or export an exact saved revision, with
   request-time revalidation of the root process. Process revision drift,
   archive or missing independent review withholds the comparison.

The service calculates **descriptive** per-case means and an *assumed* labor
cost difference per 100 cases, based on an explicitly supplied hourly rate in
currency minor units. It never multiplies estimates into "realized monthly
savings." Comparisons are withheld when either window's coverage is not
declared complete, either has fewer than 30 cases, or the person has not
reviewed the cohort selection. Even when shown, differences may reflect
seasonality, selection or changing case mix. There is no distribution,
confidence interval, source receipt verification, cost-offset accounting
or attribution to the intervention.

## Authenticated API (organization process portal)

All endpoints use the existing human OIDC identity and
`org:process:read`/`org:process:write` scopes, the shared process's owner /
editor controls, existing audit database and its revocation behavior.
There are no service-token, MCP or recorder-token fallbacks.

- `GET /v1/human/processes/{workflow_id}/value-measurements[?revision=N]`
- `POST /v1/human/processes/{workflow_id}/value-measurements`
- `GET /v1/human/processes/{workflow_id}/value-measurements/export?revision=N`
- `POST /v1/human/processes/{workflow_id}/value-measurements/delete`

POST uses `Save` from `shared/process_value_measurement.py`. It requires
`measurement`, `expected_revision` (0 initially),
`expected_access_revision`, `user_confirmed=true`, and
`organization_sharing_confirmed=true`. All request models reject extra fields,
including any attempt to set `realized_savings_verified` to true.
Delete additionally requires an exact current measurement revision,
current process access revision and owner/admin confirmation.

Measurements share the catalog's existing `workflow_knowledge` storage
under a separate, organization-scoped namespace; they are removed atomically
when their root process is deleted. No new startup migration or background
source polling is introduced.

## Future acceptance work

This is a **measured-baseline substrate**, not full roadmap #370 R16 parity.
True realized-outcome functionality still needs provider-authenticated,
authorized result receipts; comparable cohort and case-mix controls; review
of adoption, error, runtime and human costs; uncertainty intervals; distinct
outcome vs action status; attribution and overlap controls; and held-out
customer pilots. This feature must not be represented as proof of benefit
until those gates have independent evidence.
