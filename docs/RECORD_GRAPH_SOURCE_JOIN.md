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

A merged pull request is *GitHub-reported*, not proof a customer issue was resolved, an invoice paid, a refund issued, or that the outcome was good. Source-system outcomes outside GitHub are currently unsupported. **Cross-person handoff remains unverified even if two actors touched the same record.**

## Threat model and guards

- No arbitrary hosts or shell string execution: the adapter accepts only a validated stored GitHub PR locator, and constructs a fixed `github.com` URL for `gh pr view`.
- The same opaque reference must appear in retained canonical evidence before any source call is eligible. Conflicting provider/type evidence blocks refresh.
- Every API request passes OWG's existing local API/dashboard authentication. No new user grants, silent sync, historical upload, or cloud training permissions.
- Evidence deletion or the expiry of canonical events makes that record unavailable for further source reads. No audit receipts are stored by this feature.
- This is not a full general-purpose process-mining or automation system. The project still needs provider-scoped enterprise authorization, signed/provenance-aware audit receipts, versioned outcome schemas, explicit handoff transitions, retention controls for joined data and credible A/B/C evaluations.

## After this slice

Issue #226 should continue with separately reviewed integrations and tests, starting with an authenticated helpdesk/billing audit adapter that can observe actual case closure/refund/invoice status. Never infer source outcomes from observed clicks or agent summaries. Record a receipt with source, time and verification semantics; never turn actor co-presence into a handoff without a source-system ownership transition.
