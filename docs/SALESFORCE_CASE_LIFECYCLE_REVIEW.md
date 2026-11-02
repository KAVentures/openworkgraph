# Salesforce Case lifecycle review (read-only, selected scope)

**Issues:** [#326 Section B](https://github.com/KAVentures/openworkgraph/issues/326), [#264 Stage 3](https://github.com/KAVentures/openworkgraph/issues/264), [#370 R3](https://github.com/KAVentures/openworkgraph/issues/370). This is an operator-invoked, source-side **deletion detection/review** slice, not a retention executor, Salesforce customer certification, or full connector lifecycle.

## What the operator can do

An explicitly consenting operator with an approved **read-only Salesforce OAuth bearer** can inspect the current lifecycle visibility of **1–8 selected Salesforce Case IDs** using a single authenticated `GET /services/data/v60.0/queryAll`. The fixed-field query requests **only** `Id` and `IsDeleted` from `Case`. Salesforce `queryAll` may expose soft-deleted rows still retained by Salesforce; it is not proof of hard deletion, comprehensive access or historical completeness.

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

## Safety and completeness

The tool validates exact Salesforce HTTPS host and namespace/case scope before the request; it does not use system proxies or follow redirects. It accepts only a complete single-page JSON response, matching count and selected distinct IDs, exact boolean deletion states, and at most 64 KiB. It refuses malformed/duplicate JSON keys, foreign rows, inconsistent counts, pagination, HTTP errors, revoked credentials, unsafe/private path failures and output overwrite. No automatic retry. A query returning zero rows is an **all-unresolved review**, not a deletion certificate.

A source record can change immediately after read; the snapshot is not an ongoing watch or atomic comparison with OWG. Salesforce soft-deletion visibility depends on the provider's retention and permission policies. Operators must rotate/revoke source credentials and apply local artifact retention and secure deletion policies; this implementation does not manage either. Source completeness, unseen records and downstream deletion state remain unknown.

## Verification and rollout gates

`tests/test_salesforce_case_lifecycle_review.py` uses a disposable **real local TCP** Salesforce-shaped `queryAll` endpoint and checks active/deleted/missing distinction, namespace-compatible hashed case IDs, consent, permissions, malformed/partial results, revoked bearer, redirect and privacy/no-write behavior. The local loopback HTTP flag exists **only** for disposable acceptance; production requires a confirmed Salesforce HTTPS hostname. Synthetic transport does not constitute a live customer provider test. Before merging or claiming production readiness, require exact-head native Ubuntu/macOS/Windows pytest, browser, collaboration, scale, accessibility, starter and packaging/release checks and an authorized Salesforce sandbox run with appropriate scopes.
