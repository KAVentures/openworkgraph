# Issue #226 — independent acceptance runbook

**Purpose:** test source authorization, record identity, portable evidence,
and actual incremental agent value before considering #226 complete. The
GitHub CI suites exercise synthetic fixtures and package launches; they are
not substitutes for this signed-device/provider/model acceptance.

Do this on a disposable, consented test organization and test computers.
Use synthetic tickets, refunds and invoices. Do not insert clinical records,
employee private messages, customers' personal data or real payment details.

## Preparation

- Confirm `main` contains #236 (the seeded Gateway E2E integration test);
  #240, #241 and #242 must have green CI and be deployed in a throwaway test
  installation before testing their new features.
- Record the actual tested commit SHA, install artifact hash, OS version,
  provider sandbox account ID (keep credentials private), connected actor/role,
  and start/end timestamps. Do not commit sensitive test evidence to GitHub.
- Keep normal capture, prior-history sync, local AI access and source reads at
  their defaults until each is explicitly enabled under the intended test.
  Never enable `OWG_ENABLE_HISTORY_BACKFILL` in production during this test.

## 1. Native signed desktop + restricted Stripe TEST account (#240)

Use an actual signed/notarized macOS package and a packaged Windows release
launched as a **fresh standard user**, with no environment secrets set.
Under Advanced → Organization → Manage Stripe read-only source:

1. Supply the synthetic Stripe TEST account's **restricted** read key, not
   an unrestricted account secret. The account-check GET must confirm the
   exact expected account; a wrong account or insufficient read scope must
   deny the connection.
2. Observe an exact invoice/refund record via the consented browser sensor.
   Ask an authenticated local AI/client to retrieve its record graph and
   explicitly refresh source status. Confirm account and record IDs match
   their source-of-truth records; no guessed native record IDs may resolve.
3. Verify invoice open/paid and refund pending/succeeded, separately from
   external bank settlement or customer acceptance. Never count a click
   or UI submit as a provider-confirmed result.
4. Restart via Finder/Start Menu (no shell variable injection) and repeat
   with the Keychain/Credential Manager access explicitly granted and denied.
5. Disconnect the source, rotate and revoke the key at Stripe, revoke local AI
   access, and retry. All source reads must fail closed after revocation. A
   locked vault must not silently fall back to a plaintext key.
6. Inspect device logs, browser evidence, MCP responses, Gateway ingested
   rows and crash diagnostics for the **absence of secrets**. Do not publish
   real secrets in bug reports; report only yes/no and sanitized hashes.

Platform acceptance is **not** implied by macOS/Windows CI tests that use
mocked stores. If Keychain prompts or Credential Manager persistence differ,
fix the implementation before removing draft status.

## 2. Jira authorized source actions (#242)

The current Jira adapter has a **default-OFF, operator-provisioned,
short-lived OAuth proof-of-concept**, not a user-facing managed 3LO connector.
Atlassian requires a single **OWG-owned** distributable 3LO application;
do not ask customers to register custom 3LO apps or paste individual tokens.
The app needs a configured callback, resource/site-limited consent where
available, `read:jira-work` and `offline_access`, and proper rotating
refresh-token storage. That end-to-end managed flow remains unfinished.

For a consenting throwaway Jira test organization with authorized
short-lived credentials supplied privately to the local operator:

1. Test correct and incorrect Jira tenant/cloud IDs; a wrong tenant, revoked
   grant or absent read scope must fail **before** an issue read.
2. Capture one assignment in a synthetic ticket, then a later status change
   by the *new* assignee with the same stable Jira account ID. Verify source
   audit refs and HMAC-pseudonymous actor refs, without actual account IDs.
3. Repeat with the status change performed by someone else, earlier/same
   timestamp, missing author ID, invalid status transition and truncated/
   unavailable changelog. Positive corroboration must disappear.
4. In every case verify that `accepted_by_new_assignee`,
   `work_completed_by_new_assignee` and `verified_human_handoffs`
   remain false/zero. A status change is later activity, **not** a handshake
   or verified customer result.
5. Verify cross-tenant isolation and source timeout/revocation behavior.

## 3. Actual A/B/C agent experiment (#241)

Prepare held-out, independently adjudicated case truth separately from the
agent-visible scenario file. Both source snapshots and OWG snapshots must
use the **same exact opaque record ref**. Cover interleaving, absent refs,
outages, staleness, divergent clocks, inconsistent IDs and cross-device data.

First run the **zero-cost** manifest preflight:

```bash
python scripts/run_issue226_agent_study.py \
  --cases /private/owg_eval/cases.json \
  --scenarios /private/owg_eval/scenarios.json \
  --validate-only
```

Then execute an actual logged-in Codex or Claude Code CLI through the
shipped adapter, from an isolated test account and with deliberate,
appropriately restricted localhost-read permission:

```bash
python scripts/run_issue226_agent_study.py \
  --cases /private/owg_eval/cases.json \
  --scenarios /private/owg_eval/scenarios.json \
  --agent-cmd "python /absolute/path/to/scripts/issue226_model_cli_adapter.py --provider codex" \
  --replicates 5 --seed 2026 \
  --runs-output /private/owg_eval/runs.json \
  --report-output /private/owg_eval/report.json
```

Condition A sees only baseline tools, B the same source snapshot that C
receives, and C additionally the OWG record. Successful broker requests
produce host-observed evidence receipts. Wrong ref, no ref, and denied
permissions must not generate a false successful OWG receipt. An additional
run with `--owg-local-url http://127.0.0.1:8787` can use the actual local
read-only OWG API when the parent runner has a valid local API credential;
this is **not** equivalent to native MCP tool instrumentation.

Pin the same model/version/settings across arms; isolate each trial, randomize
arm order, independently adjudicate outcome success and consequential wrong
actions, and manually validate that any reported improvement exists beyond
system logs alone. Synthetic pre-canned test agents **do not** prove agent
utility. The offline scorer does not claim superiority.

## 4. Gateway privacy and failure regression

Exercise a revoked device, excluded/paused intervals, pre-enrollment
history default OFF, explicit historical date range/preview, source policy
change between preview and confirmation, concurrent token consumption,
post-deactivation Discovery exclusions, and cross-tenant/actor read isolation.
Confirm ordinary forward cursor never rewinds and no test uses real private
employee data. #236's seeded Gateway acceptance test covers many synthetic
variants; real consented provider acceptance is still separate.

## Release / closure gate

Do **not** close #226 or switch default-OFF flags based solely on passing CI,
mocked provider calls or a synthetic agent fixture. Close only after
documented organizational source-grant connect/refresh/revoke, real test
provider receipts, explicitly verified human acceptance when claimed,
signed desktop vault behavior, multi-tenant privacy, and an independently
reviewed agent study demonstrating meaningful incremental value. Distinguish
still-unsupported claims in the user-facing product.
