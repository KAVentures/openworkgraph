# Record graph, initial source-joined slice (issue #226)

This endpoint builds a **read-only, record-scoped evidence view** from existing local OpenWorkGraph event data. It distinguishes active work, source-system audit evidence, and verified business outcomes. These are separate concepts.

`GET /v1/record-graph?resource_ref=owg:r:…&include_source_status=true` requires normal authenticated local API/dashboard read access. It does **not** expose a new public/hosted endpoint or write data.

Local MCP connections (stdio and the locally authenticated MCP HTTP transport) additionally expose `get_record_graph(resource_ref, include_source_status=false, since, until)`. This uses the existing per-AI-app consent check, redaction pipeline, history date-range policy and evidence auditing. The hosted cloud plugin is deliberately unchanged; no cross-device or cross-employee resolver is inferred.

## What is supported today

- The returned timeline contains only canonical event IDs, UTC/source timestamps, structural event types and source channels. It does not include page contents, browser paths, clipboard values, customer names, titles or native business identifiers.
- Interleaved records are grouped by exact, previously observed, normalized `owg:r:` references. Opaque reference capture was already opt-in before this PR. No identity link is inferred from temporal proximity or title similarity.
- The GitHub PR adapter can retrieve the **current source-reported PR state and bounded review action history** using the local user's existing authenticated `gh` CLI. Finder-launched signed macOS builds search `/opt/homebrew/bin/gh` and `/usr/local/bin/gh` after PATH; `OWG_GH_CLI_PATH` can explicitly point to an existing executable if installed elsewhere. The shared resolver also fixes the existing opt-in PR outcome tracker. It never returns author identities, comments, source locators or credential material.
- External reading happens **only** with `include_source_status=true` **and** the existing **GitHub outcome tracking opt-in enabled**. The endpoint itself cannot grant that setting. If opt-in is off, the locator is absent, the record is not observed, `gh` fails, or the source is unsupported, the adapter returns `unknown`; it never falls back to an inferred outcome.
- All source reads are synchronous and read-only, no new scheduled polling or persistent derived database, and the status can immediately become stale. Returned `verified_at` is a refresh timestamp, not a warranty.

A merged pull request is *GitHub-reported*, not proof a customer issue was resolved, an invoice paid, a refund issued, or that the outcome was good. Jira Cloud issue status is also supported when the local user explicitly opts in and a tenant-matching source record is available. This verifies the issue state in Jira, not any downstream refund, payment or customer acknowledgement. **Cross-person handoff remains unverified even if two actors touched the same record.**

## Threat model and guards

- No arbitrary hosts or shell string execution: the adapter accepts only a validated stored GitHub PR locator, and constructs a fixed `github.com` URL for `gh pr view`.
- The same opaque reference must appear in retained canonical evidence before any source call is eligible. Conflicting provider/type evidence blocks refresh.
- Every API request passes OWG's existing local API/dashboard authentication. No new user grants, silent sync, historical upload, or cloud training permissions.
- Evidence deletion or the expiry of canonical events makes that record unavailable for further source reads. No audit receipts are stored by this feature.
- This is not a full general-purpose process-mining or automation system. The project still needs provider-scoped enterprise authorization, signed/provenance-aware audit receipts, versioned outcome schemas, explicit handoff transitions, retention controls for joined data and credible A/B/C evaluations.

## After this slice

Issue #226 should continue with separately reviewed integrations and tests, starting with an authenticated helpdesk/billing audit adapter that can observe actual case closure/refund/invoice status. Never infer source outcomes from observed clicks or agent summaries. Record a receipt with source, time and verification semantics; never turn actor co-presence into a handoff without a source-system ownership transition.


## Experimental Jira Cloud source-of-truth adapter (issue #226, phase 2)

The local record graph can now include current Jira issue status and a bounded
issue changelog for already-observed Jira references. This is an experimental
local-only source adapter, not an enterprise organization connector.
**Every setting below is OFF/absent by default**; the app never asks connected
agents for credentials and never uploads secrets to OWG cloud.

The local service process must explicitly receive all four settings:

- OWG_JIRA_SOURCE_READ_ENABLED=true: separate opt-in.
- OWG_JIRA_HOST=your-tenant.atlassian.net: exact validated Jira tenant.
- OWG_JIRA_CLOUD_ID: OAuth-accessible Jira site UUID.
- OWG_JIRA_OAUTH_ACCESS_TOKEN: a **short-lived OAuth 2.0 bearer token**
  obtained through an independently authorized Atlassian 3LO app.

The adapter **does not accept individual API tokens or user email/password
credentials**. It first checks Atlassian's live OAuth accessible-resources API
and requires the exact configured site/cloud ID and read:jira-work grant
before issuing any issue/changelog request. Redirects are refused; token
requests target the fixed api.atlassian.com origin, and both time and response
size are bounded. Revocation, inaccessible sites and wrong/missing scopes fail
closed. No token is stored by OWG.

Do not paste OAuth tokens into a prompt or source repository. Finder launches
do not inherit shell variables, so this is still an **operator-provisioned
local prototype**. A proper commercial rollout requires OWG-owned Atlassian
OAuth connection/refresh/revocation UX with administrator-approved per-org
grants and secure token storage. Those steps are NOT implemented here.

Authorized reads are made only for a retained, already-observed normalized
Jira issue and only when include_source_status=true was specifically
requested. The stored local resolver must match the tenant exactly. Calls use HTTPS on the fixed api.atlassian.com origin after verifying the
configured Jira tenant/cloud ID against live OAuth authorized resources;
redirects are disabled and request size/time are bounded. No new data is recorded or synchronized.

The return value distinguishes:
- **current Jira status category** (new/in-progress/done), as reported by Jira;
- **resolved issue** only if Jira reports "done", a resolution object and a
  valid resolution timestamp. This is a source-reported operational completion,
  not a verified payment/refund or customer acknowledgement;
- **assignee transitions** with local HMAC pseudonyms and observed timestamps;
  an assignment is **not** proof of handoff acceptance or task success;
- **bounded status change audit** and whether the retrieved page is complete.
  A partial/missing changelog is never interpreted as no handoffs or rework.

No summaries, usernames, raw source IDs, email bodies or ticket descriptions
are returned. Failed permissions, unknown/mismatched records, source outages
and inconsistent resolution fields return unknown instead of guesswork.

Remaining issue #226 stages: org-scoped OAuth authorization, cross-system
admin-approved mappings and audit provenance, helpdesk/billing-specific outcomes
(refunds and invoice payment), granular retention/deletion of joined receipts,
true handoff acceptance corroboration, broader export/navigation UX, and a
larger agent A/B/C benchmark that logs whether the OWG tool was called.


## Bounded Jira post-assignment activity corroboration

When an opted-in Jira changelog has a validated **assignee change** and later
a separate **status transition**, and Atlassian attributes that later status
change to the *same new assignee's stable account ID*, OWG can report
`subsequent_status_action_by_assignee_observed: true` with the two independent
pseudonymous source-audit references. This is an additional trace of provider-
recorded work after reassignment, not a source assertion of a human handshake.

The authorized Jira response is bounded, with HMAC pseudonyms for raw account
IDs; names, comments, issue keys and account IDs are never returned. A
status action by a different account, a simultaneous/earlier timestamp, a
status item lacking validated from/to status IDs, a missing author account ID,
or a missing/partial changelog **never** provides positive corroboration.

`accepted_by_new_assignee`, `work_completed_by_new_assignee` and
`verified_human_handoffs` intentionally remain false/zero even with this
evidence. `absence_of_corroboration_proves_no_work` is always false.
The specific measure is a bounded source-audit observation, **not** customer
outcome verification or an accepted cross-person handoff. An independent
acknowledgement/acceptance receipt still requires a different authorized
source-system event and must be validated separately.


## Production Jira OAuth dependency before real organization tests

Atlassian's documented 2026 3LO security guidance says an app should use one
**distributable application-owned OAuth 2.0 integration**, rather than asking
each customer to create an individual custom OAuth app or supply personal API
tokens. OWG therefore needs a single developer-console OAuth application,
a registered callback URL controlled by OWG, the minimum `read:jira-work`
scope, and `offline_access` for **rotating refresh tokens**. Atlassian's
resource-level consent is preferable where site-specific authorization is
available.

The current operator-injected `OWG_JIRA_OAUTH_ACCESS_TOKEN` proof-of-concept
does NOT implement a callback, credential storage or refresh lifecycle.
Do **not** publish a setup tutorial telling each user to create a Jira 3LO
app or paste their personal API token. Implement the company-managed callback,
org-bound consent record, securely stored refreshed credentials, immediate
revocation and per-source site/scope checks before commercial rollout. The
OWG-owned OAuth client ID/secret and verified callback configuration are
deployment prerequisites; they are never included in this repository or
passed through a connected AI tool.

Official guidance:
https://developer.atlassian.com/cloud/jira/software/oauth-2-3lo-apps/
https://developer.atlassian.com/cloud/oauth/getting-started/refresh-tokens/


## Exact-record history lookup (additive hardening)

Local `get_record_graph` and `GET /v1/record-graph` now select events by the
**exact normalized opaque resource reference** from the retained canonical event
store before applying the bounded record-event limit. Previously the route read
the newest 25,000 events across **all** activities first: older valid records
could appear absent even while retained, and interleaved history could hide a
contradictory source type.

- The query uses parameterized SQL, a substring prefilter and exact validated
  JSON-path equality. A reference appearing only in text or another JSON field
  is not evidence of a business record. No source credentials or native record
  values are returned.
- Existing `since`/`until` restrictions and local AI-history permission
  boundaries remain in force. This change cannot retrieve deleted evidence,
  change consent, or extend retained history.
- `source_event_limit` now bounds **matching record events**, not unrelated
  global work events. The lookup reads one extra match to distinguish an exact
  limit from truncation.
- If a record's matched events exceed the limit, OWG marks identity completeness
  false and refuses a live provider status refresh: older evidence might contain
  a conflicting provider/record type. Call again with a narrower permitted date
  window to review the record safely.
- Additive fields `source_rows_scoped_to_exact_record` and
  `record_identity_complete_within_retained_window` explain this limit.
  `source_event_limit_reached` now indicates **actual** truncation rather
  than equating an exactly full page with overflow.
- No index migration is added; this remains an on-demand read and may scan
  the retained local event table on large installations. Benchmark before
  introducing a persisted reference index, and never block recorder startup
  with an unbounded migration.

This is a prerequisite for future optional Open Work/case discovery, **not**
an Open Work engine. It neither claims missing business obligations nor scans
external systems for unseen tasks. Existing Activity, Workflows, repeated-family
discovery, Discovery Mode, exports, policy, privacy and agent interfaces
remain intact. Additional business-case or obligation features should be
optional, separately authorized, and independent of workflow mapping.
