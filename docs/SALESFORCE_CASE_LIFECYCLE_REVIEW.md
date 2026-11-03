# Salesforce Case lifecycle review (read-only, selected scope)

**Issues:** [#326 Section B](https://github.com/KAVentures/openworkgraph/issues/326), [#264 Stage 3](https://github.com/KAVentures/openworkgraph/issues/264), [#370 R3](https://github.com/KAVentures/openworkgraph/issues/370). This is an operator-invoked, source-side **deletion detection/review** slice, not a retention executor, Salesforce customer certification, or full connector lifecycle.

## What the operator can do

An explicitly consenting operator with an approved **read-only Salesforce OAuth bearer** can inspect the current lifecycle visibility of **1–8 selected Salesforce Case IDs** using **1–8 explicitly bounded authenticated GET requests** to `/services/data/v60.0/queryAll` and Salesforce's same-origin query locator continuation. The fixed-field query requests **only** `Id` and `IsDeleted` from `Case`. Salesforce `queryAll` may expose soft-deleted rows still retained by Salesforce; it is not proof of hard deletion, comprehensive access or historical completeness.

The tool creates a **new, mode-0600, owner-private JSON review** listing stable OWG-compatible namespace-hashed case IDs and one of:

- `active`: a selected source Case row was returned with `IsDeleted=false`.
- `soft_deleted`: a selected source Case row was returned with `IsDeleted=true`.
- `unresolved`: no selected Case row was returned. It may be inaccessible, purged, outside retention, absent or otherwise hidden; **never infer deleted**.

No raw Case IDs, credentials, Salesforce host, business values or account namespace are written to the review or printed in the counts-only receipt. Hashed IDs are stable pseudonyms, **not anonymization**. The artifact records its source scope SHA-256, UTC read time, explicit unknown completeness, per-case state, counts and non-write flags. The CLI prints a SHA-256 digest of the private artifact for subsequent manual review.

## Run

Choose an owner-only directory (`chmod 700` on POSIX), a confirmed Salesforce instance and a stable namespace **identical to the existing CaseHistory import namespace**. Do not reuse output paths. Store the current least-privilege bearer in an environment variable, not in a CLI argument or log.

```sh
mkdir -m 700 /private/owg-salesforce-lifecycle
export OWG_SALESFORCE_READ_TOKEN='<approved-read-only-bearer>'
python -m server.salesforce_case_lifecycle_review \
  --instance https://acme.my.salesforce.com \
  --confirm-host acme.my.salesforce.com \
  --namespace acme-sandbox \
  --case 500000000000001 \
  --case 500000000000002 \
  --output /private/owg-salesforce-lifecycle/review-001.json \
  --consent-source-lifecycle-read --consent-private-review
```

Review the resulting file locally before deciding what, if anything, to do about source lifecycle changes. **There is deliberately no automatic OWG event deletion, retention change, checkpoint advancement, employee identity mutation, Salesforce write or agent handoff.** Follow OWG's separately authorized deletion/retention procedure where applicable. This command does not grant deletion approval.

## Compare two reviewed source snapshots (optional)

The read-only command can now compare **a previously saved review** with a fresh bounded Salesforce read for the **same confirmed instance, namespace and exact selected Cases**. This is a useful operator-facing change review: it distinguishes a Case newly observed as soft-deleted, a Case no longer visible, a Case becoming visible again and a soft-deleted Case appearing active. A change is an observation between two reads, **not** proof of when it occurred, a complete Salesforce audit trail, a hard-deletion finding or authorization to delete OWG evidence.

Keep the first private JSON review and copy its printed `review_sha256` receipt into the next invocation. Use the same source scope and add the separate explicit comparison consent:

```sh
python -m server.salesforce_case_lifecycle_review \
  --instance https://acme.my.salesforce.com \
  --confirm-host acme.my.salesforce.com \
  --namespace acme-sandbox \
  --case 500000000000001 \
  --case 500000000000002 \
  --previous-review /private/owg-salesforce-lifecycle/review-001.json \
  --previous-sha256 '<exact-review_sha256-from-first-receipt>' \
  --output /private/owg-salesforce-lifecycle/review-002.json \
  --consent-source-lifecycle-read --consent-private-review \
  --consent-compare-previous-review
```

The previous review must be a canonical owner-private regular file (not a symlink) with a matching SHA-256, complete version-1 schema, same scope hash and exact namespace-hashed Case set, internally consistent states/counts, valid timestamp and non-write flags. Validation happens **before any Salesforce GET**. A previous comparison review can itself be used for the next comparison with its newly printed digest. Existing single-snapshot invocation remains unchanged.

The new private review contains `comparison.previous_review_sha256`, the prior UTC observation timestamp and one pseudonymous per-Case `previous_state` → `current_state` transition (including unchanged states). The CLI prints only changed/unchanged totals and the pinned previous digest. The current review retains the current `case_states` and three state counts. No raw source IDs, host, token or namespace are included. A SHA-256 pin detects unexpected edits **relative to the operator's trusted receipt**; it is not an authenticated signature, a proof of provider authenticity or a substitute for protecting the receipt and local files. Do not use these artifacts to automatically alter retention, revoke accounts, delete evidence or advance an import checkpoint.


## Review retained OWG evidence for the selected source lifecycle (optional)

After creating a source lifecycle review, an authorized local OWG operator can **independently read the actual local event-log workspace** and identify which selected Salesforce Cases still have retained historical events. This is a second, separately consented, **read-only** step: it does **not** contact Salesforce again, import events, alter a checkpoint, delete a log or authorize retention changes. It is useful when a source Case is observed soft-deleted or no longer visible but the local OWG history still contains records. A missing OWG case is **not** proof that all evidence has been deleted.

Use a current local OWG dashboard session, the **exact** existing log ID and the same confirmed Salesforce host, namespace and selected Cases used for the pinned source review. Set the OWG session in the process environment, never in the command line:

```sh
export OWG_DASHBOARD_SESSION='<current-local-dashboard-session>'
python -m server.salesforce_case_lifecycle_review \
  --instance https://acme.my.salesforce.com \
  --confirm-host acme.my.salesforce.com \
  --namespace acme-sandbox \
  --case 500000000000001 --case 500000000000002 \
  --impact-review /private/owg-salesforce-lifecycle/review-001.json \
  --impact-sha256 '<exact-review_sha256-from-source-receipt>' \
  --owg-log-id log_0123456789abcdef \
  --confirm-owg-log-id log_0123456789abcdef \
  --owg-port 8765 \
  --output /private/owg-salesforce-lifecycle/impact-001.json \
  --consent-local-owg-read --consent-private-impact
```

The CLI validates the owner-private, canonical source review, operator-pinned SHA-256, full source scope, pseudonymous Case set, non-write flags, exact log ID and current session **before making any local OWG HTTP request**. It then uses only the existing dashboard-authenticated `GET /v1/event-logs` and `POST /v1/event-logs/{log_id}/cases/query?case_type=source_case` endpoints at literal IPv4 loopback, with proxies and redirects disabled. The log must have **exactly one** matching `sf_<scope-hash-prefix>` source/case namespace. At most eight case queries, each limited to 100 returned case summaries and 64 KiB, inspect retained event counts without retrieving activities, resources or individual event contents. The session and exact source namespace are checked again after all case reads and before writing the artifact; this reduces stale-scope risk but is not an atomic database snapshot. Local session revocation, source mismatch, malformed or truncated responses refuse the entire output.

The new mode-0600 private JSON review records each stable hashed Case ID, source state, local evidence status (`observed`, `not_observed` or `unknown`) and retained event count **only when observed**, plus aggregate counts of soft-deleted/unresolved source Cases with observed OWG evidence. A bounded query that cannot determine a case's presence is `unknown`, not zero. The printed receipt is counts-only with a SHA-256 pin; the private file contains the local log ID but **no raw Salesforce IDs, host, bearer, session, activities, resources or source account namespace**. Hashed case IDs are still pseudonymous and should be retained/deleted under organizational policy.

The source lifecycle snapshot and local OWG evidence are **not atomic**; both can change after their respective reads. OWG case presence is only locally retained history, not a live provider verification or a full retention inventory. No absence is evidence of hard deletion. An authorized human must separately assess retention, legal hold, deletion, access controls and the provenance of any requested downstream action. There is deliberately no automated deletion, approval, policy mutation or agent handoff.

## Compare two retained-evidence impact reviews (optional)

The operator can now **re-read actual local OWG evidence** for the same confirmed Salesforce source scope and **same OWG log**, then compare that result with an earlier pinned impact artifact. This closes the review loop for changes in retained event counts or source visibility, without guessing why a record appeared or disappeared. It is useful for checking whether a previously observed soft-deleted Case still has OWG history, whether more events have arrived after an append, and whether an unresolved source Case later becomes visible.

First create a private impact review as above and retain its printed `impact_sha256`. Optionally refresh the Salesforce source lifecycle review with a separate source-read command; the comparison accepts a *new* source-review digest provided that its exact selected Case scope is unchanged. Then run:

```sh
export OWG_DASHBOARD_SESSION='<current-local-dashboard-session>'
python -m server.salesforce_case_lifecycle_review \
  --instance https://acme.my.salesforce.com \
  --confirm-host acme.my.salesforce.com \
  --namespace acme-sandbox \
  --case 500000000000001 --case 500000000000002 \
  --impact-review /private/owg-salesforce-lifecycle/review-002.json \
  --impact-sha256 '<new-source-review-sha256>' \
  --previous-impact /private/owg-salesforce-lifecycle/impact-001.json \
  --previous-impact-sha256 '<exact-previous-impact-sha256>' \
  --owg-log-id log_0123456789abcdef \
  --confirm-owg-log-id log_0123456789abcdef \
  --owg-port 8765 \
  --output /private/owg-salesforce-lifecycle/impact-002.json \
  --consent-local-owg-read --consent-private-impact \
  --consent-compare-impact
```

The prior impact must be an owner-private, non-symlink regular file with canonical JSON and the operator-pinned SHA-256. Before making **any OWG HTTP request**, the tool checks its complete schema, safety flags, timestamps, exact source scope, OWG log ID, selected pseudonymous Case IDs, internally consistent case evidence/counts, and any earlier comparison. It rejects changed source scope or OWG log, altered files even with a recomputed digest when the schema is inconsistent, missing separate comparison consent, and missing/revoked dashboard sessions. Current evidence is then read through the same production-authenticated local FastAPI/DuckDB endpoints as a first impact review; authorization and source identity are rechecked after the bounded reads.

The new private JSON contains `comparison.transitions` for each selected hashed Case ID: prior/current source state, prior/current OWG presence (`observed`, `not_observed`, `unknown`) and prior/current retained event count (integer only when observed). It also contains `changed`, `unchanged`, `source_state_changed`, `owg_presence_changed`, and `owg_event_count_changed` totals, with the previous impact/source digests and observation time. The CLI prints only aggregate totals and the new `impact_sha256` pin. A comparison review can be reused as the next prior impact with its newly printed digest.

**Interpretation limits:** `not_observed` is a bounded local case-query result, not a deletion certificate; `unknown` is not zero; a change in event count is not a verified source-side change or retention action. The two reads are non-atomic. Source visibility, OWG log content and authorization can change after each read. Neither digest authenticates the provider or signs the reviewer identity. The comparison performs **no** Salesforce or OWG write, checkpoint advancement, automatic retention/deletion, legal-hold decision, or agent handoff. Customer sandbox acceptance and policy/retention review remain separate gates.

## Safety and completeness

The tool validates exact Salesforce HTTPS host and namespace/case scope before the request; it does not use system proxies or follow redirects. It defaults to one page; operators must explicitly set `--max-pages 2` through `--max-pages 8` when Salesforce may paginate. Each page is limited to 64 KiB; the entire selection is limited to 8 distinct Cases and 8 requests. A continuation must be a strict relative `/services/data/v60.0/query/<opaque-locator>` or `/services/data/v60.0/queryAll/<opaque-locator>` path on the **same confirmed origin**, with no query, fragment, redirect or path traversal. It requires stable `totalSize`, strictly ordered distinct selected IDs, exact boolean deletion states, nonempty intermediate pages and a final `done=true` page with no cursor and exact cumulative count. It refuses malformed/duplicate JSON keys, foreign rows, changed counts, truncated/over-budget/repeated/unsafe cursors, HTTP errors, revoked credentials, unsafe/private path failures and output overwrite. If a provider returns more pages than the operator approved, the run fails **without creating a review**. The private artifact and counts-only receipt record `pages_fetched` for auditing. No automatic retry. A query returning zero rows is an **all-unresolved review**, not a deletion certificate.

A source record can change immediately after read; the snapshot is not an ongoing watch or atomic comparison with OWG. Salesforce soft-deletion visibility depends on the provider's retention and permission policies. Operators must rotate/revoke source credentials and apply local artifact retention and secure deletion policies; this implementation does not manage either. Source completeness, unseen records and downstream deletion state remain unknown.

## Verification and rollout gates

`tests/test_salesforce_case_lifecycle_review.py` uses a disposable **real local TCP** Salesforce-shaped `queryAll` endpoint (including multi-page cursor continuation) and checks active/deleted/missing distinction, namespace-compatible hashed case IDs, consent, permissions, malformed/partial results, revoked bearer, redirect and privacy/no-write behavior. The impact-mode acceptance additionally exercises a pinned real source review and actual dashboard-session-authenticated FastAPI/DuckDB case-summary routes over local TCP, including wrong-source scope, revoked session, tampering, consent and non-write behavior. The local loopback HTTP flag exists **only** for disposable acceptance; production requires a confirmed Salesforce HTTPS hostname. Synthetic transport does not constitute a live customer provider test. Before merging or claiming production readiness, require exact-head native Ubuntu/macOS/Windows pytest, browser, collaboration, scale, accessibility, starter and packaging/release checks and an authorized Salesforce sandbox run with appropriate scopes.
