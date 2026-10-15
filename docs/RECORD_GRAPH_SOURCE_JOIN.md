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
