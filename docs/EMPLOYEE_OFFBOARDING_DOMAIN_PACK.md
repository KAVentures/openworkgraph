# R23 — Employee offboarding and access-revocation domain pack

**Pack ID:** `employee_offboarding`  
**Version:** `1.0.0`  
**Area:** Local event-log workspace → Domain packs

This adds an installable, versioned employee-offboarding template to OWG's
existing **CSV / XES / OCEL event-log** workflow. It complements the
order-to-cash, procure-to-pay, ITSM, sales and customer-onboarding packs.
Nothing is sent to an identity provider, source connector, employer, AI
agent or remote service by installing or running this pack.

## Supported analyst workflow

1. **Import an explicitly authorized event log**, using OWG's existing
   on-device retention confirmation. The log must have stable offboarding
   **case IDs** and timestamps with explicitly known timezones.
2. In **Domain packs**, select **Employee offboarding and access
   revocation** and map the required roles to the log's actual labels:
   `offboarding_case`, `offboarding_started`, and `access_revoked`.
   The optional labels are `manager_approved`, `asset_returned`,
   `review_completed`, and `case_closed`.
3. Check the described assumptions and customize the two elapsed-time
   goals to match the organization's real policy:
   `revocation_hours`, default 24h (0.25–8,760h), and
   `closure_days`, default seven days (1–365 days). The defaults are
   **not** a claim about legal, contractual or regulatory requirements.
4. **Install** the pack. OWG saves the exact version, mapping, parameter
   values and definitions digest; its five typed case metrics join the
   log's existing metric library under the pack's ownership.
5. **Run report** to examine log-derived case durations, event counts,
   missing/late revocation events, revocation before case start,
   repeated revocation records, optional approval-before-revocation,
   revocation-before-closure, optional elapsed closure target and
   segregation-of-duties evidence.
6. Reconfigure and reinstall to adjust targets. The new definitions
   receive a new digest, preserving bounded install history. Uninstall
   removes **only** the pack's metrics, leaving user-authored metrics
   untouched. Deleting the source log removes the install with it.

Each report reuses the existing object-aware case notion, typed metrics,
compliance rules and reference-model conformance implementation. It
reports `not_applicable` entries when optional mapped roles are omitted.

## Critical evidence boundaries

This is **not** a deprovisioning product or verified identity audit.
A recorded `access_revoked` event might describe a ticket or asynchronous
request, not completed revocation. An account may retain active sessions,
tokens, third-party entitlements, devices, shared secrets or dependent
credentials even when a revocation event exists. Conversely, a missing
event might reflect an incomplete export, not an active account.

The pack never calls Microsoft Entra, Okta, Google Workspace, an HRIS,
Jira, or a device-management API to verify any account state. It does not
authorize deletion, impersonation, account suspension or any business
action. It makes **no** guaranteed compliance finding.

The optional segregation-of-duties check requires observed actor/resource
identities; missing actor data is **unknown**, not satisfied. A repeated
revocation event may reflect legitimate retries or multiple application
permissions and must not be equated with duplicate business actions.
Elapsed timestamps are **not employee effort saved**.

OWG's retention and local dashboard session restrictions remain unchanged.
AI/MCP bearer tokens cannot install, run or uninstall the pack over the
dashboard-only endpoints.

## Test coverage

- `tests/test_event_log_domain_pack_employee_offboarding.py`: seven
  synthetic cases with observed normal, late, missing, out-of-order and
  repeated revocation records; exact time parameter resolution;
  evidence-only flags, unknown segregation, optional-role omissions;
  versioned reinstall, user metric preservation, source deletion and
  real ASGI import/catalog/install/report/uninstall with AI-bearer denial.
- `tests/browser/domain_packs.cjs`: the actual event-log dashboard
  imports a CSV, maps fields, saves the pack, displays the report,
  adjusts the elapsed target and reinstalls/uninstalls, alongside the
  existing packs. Includes WCAG and mobile checks.
- A feature-scoped GitHub workflow runs focused pytest and real
  Chromium/axe acceptance, without changing global CI.

## Release caveat

This feature is stacked on the separately drafted customer-onboarding
pack (#690), because both extend the same versioned registry and browser
catalog. All native exact-head tests and Chromium acceptance must pass,
and its base pack must first merge, before this pack is considered merged
or production-ready. Independent customer sample validation, source
entitlement reconciliation and real multi-team rollout are not yet proven.
