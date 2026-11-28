# Native Slack enterprise audit and selected-channel activity

Enterprise program #1572. Stacked on the shared enterprise feed runtime
(#1573), Google Workspace (#1574) and native SQL (#1575).

## Supported native sources

| Source | Provider API | Required approval |
|---|---|---|
| Enterprise audit | Slack Audit Logs API | Enterprise organization-owner user token, auditlogs:read, organization-wide read consent |
| Selected channel activity | Slack conversations.history | Exact workspace and channel, member token, appropriate history/read scopes, transient message payload consent |

Enterprise audit is available only on supported Slack Enterprise plans. It is
not a general message-monitoring API. Selected channel activity is distinct
from audit ingestion and does not enumerate every channel. It does not read
direct messages, group DMs, Slack Connect/external shared channels, or
organization-shared channels. It does not separately traverse thread replies,
or reconstruct edited/deleted messages, approvals or business outcomes.

## Deployment and operator setup

Provision an explicitly approved read-only Slack app/credential. The app
installation and consent occur at Slack, outside OWG. Supply the token to
the OWG process through a deployment secret environment variable, e.g.:

    OWG_SLACK_TOKEN_AUDIT=<organization-owner user token>
    OWG_SLACK_TOKEN_CHANNEL=<approved member bot or user token>

Do not paste tokens into the dashboard, configuration names, issue tracker,
exports or URLs. Audit mode expects an xoxp token; channel mode accepts
xoxp or xoxb tokens. An exact keyed credential binding is retained, not the
token. The deployment administrator controls provisioning and rotation; a
replacement token requires creation of a new profile in this increment.
The connector does not implement Slack OAuth installation or refresh.

In **Enterprise connector operations → Create native Slack feed**, select
the mode, exact enterprise ID for audit or exact workspace and channel IDs
for channel activity (also specify the enterprise ID for an enterprise
workspace). Select a case basis, review the window/overlap/lag and approve
local metadata retention. Audit mode additionally requires domain-wide
read consent, even with action or actor filters. Channel mode additionally
requires consent for transient receipt of message payloads: Slack does not
offer a fields mask for conversations.history.

Save creates an immutable local profile, without contacting Slack.
Confirm a single bounded window to read, or explicitly create a recurring
schedule for the current profile revision. A profile change invalidates
earlier scheduled approval. The existing recording pause, retry cooldown,
backoff and quiet-hours behavior apply to recurring work.

## Identity and authorization boundary

All HTTP requests use fixed provider endpoints, HTTPS verification, no
redirects and no environment proxies. Before and after each window the
connector calls auth.test and verifies the declared enterprise/workspace.
The authenticated principal is pinned using a profile-local HMAC on
the first completed window; future windows must match it. In channel mode
conversations.info also verifies the exact channel, membership and explicit
non-shared status before and after the read. Unknown or absent required
scope fields fail closed. Organization audit access itself is enforced by
Slack; workspace membership inside an organization audit response is not
independently enumerated. An enterprise location with a different ID is
refused.

Audit records retain a provider ID-derived pseudonym, action label,
timestamp, pseudonymous actor (if available) and a declared case:
actor, typed entity or location. Missing actor/entity identifiers fall
back to the audit event case. Slack placeholder actors are not represented
as actual workers. Optional exact actor and action filters are checked
against every returned record. Audit timestamps are whole seconds.

Channel history records retain a pseudonym of channel plus exact Slack
microsecond timestamp, a namespaced message subtype, optional pseudonymous
user/bot and either the selected channel case or reported thread identity.
A thread identifier is a grouping of returned records, not evidence that
the entire thread was retrieved. Hidden mutation notices are refused.
Raw text, files, attachments, names, email, IP, details and raw responses
are not retained. The provider may return these transiently; do not enable
history reads where this is unacceptable under the customer's policies.
The connector does not claim that content is never received by the process.

## Ingestion guarantees and limits

Both endpoints support native cursor traversal and explicit time windows.
The audit endpoint uses inclusive integer seconds; channel history uses
inclusive decimal timestamp strings with exact microsecond precision.
Overlapping windows use the existing supplied-source-key index to
deduplicate boundaries and retries. Conflicting retained payloads refuse
the append. More results at a tied audit timestamp than the requested
page limit are accepted only within the global resource budget.

Both feeds reuse the common transaction for event retention, principal
pin and checkpoint. Missing cursors when more results are declared,
repeated cursors, scope drift, malformed response, expired lease, revoked
credential, rate limit or conflicting identity fail without advancing
the checkpoint. An empty complete window can advance without creating
a fabricated dataset. Retry-After seconds are respected; HTTP-date
Retry-After is treated as the existing conservative 60-second default.
All counts and metadata are local. The unified operations inventory is
passive and exports no token, endpoint scope, actor or channel ID.

Bounds: 20 pages per window, 10,000 received records, 2 MiB per response,
16 MiB per window, 90-second nominal network budget and shared 120-second
lease. The advancing window is 1–168 hours; initial history is bounded to
366 days by OWG, not a Slack retention promise. A configurable overlap and
lag help with delayed records; later arrivals outside overlap may be
missed. A provider with restrictive rate limits may require a smaller
window; this implementation does not persist partial provider cursors.
A repeatedly oversized window requires a new profile with tighter
bounds rather than an implicit scope change. Slack Marketplace/non-
Marketplace and customer-internal app limits differ. No completeness,
production performance or live-provider acceptance is established.

## Validation

- `tests/test_slack_native_feed.py`: mock native HTTPS response contracts,
  actual retained DuckDB operations, organization/workspace/channel scope,
  exact event identity, microsecond precision, filters, fallback case,
  pagination, revocation, principal change, retry cooldown, privacy
  projection, recurring authorization, HTTP and passive inventory.
- `tests/browser/enterprise_connectors.cjs`: actual dashboard setup,
  scope-specific form controls, retained profile removal and sanitized
  operations download without real Slack network calls.
- Existing CI runs the full regression suite and Chromium acceptance when
  the stacked PR is ready for review.

Official reference:
- https://docs.slack.dev/admins/audit-logs-api/
- https://docs.slack.dev/reference/audit-logs-api/methods-actions-reference/
- https://docs.slack.dev/reference/methods/auth.test
- https://docs.slack.dev/reference/methods/conversations.info/
- https://docs.slack.dev/reference/methods/conversations.history/
- https://docs.slack.dev/apis/web-api/rate-limits/

Remaining: provider sandbox acceptance on appropriate plans, OAuth
installation/refresh and exact-account credential rebind, managed secrets,
approved thread traversal and additional channel sources, deletion/edit
reconciliation, Gateway-managed execution and enterprise-scale validation.
