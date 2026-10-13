# Device-first evidence upload: privacy migration and release gate

## Current status (PR #196)

The old Gateway workflow stored the title/metadata as received by `/v1/evidence/batch`, then applied a separate redactor when a public AI queried it. This is a meaningful cloud-at-rest data exposure: answer-time redaction does not eliminate source personal data already persisted.

The new ordinary **structural evidence** path runs an independent, deterministic on-device projection in `connector/sync.py`, after local/organization policy intersection and **before** `httpx.Client.post`:

1. Build only approved structural fields; do not send arbitrary nested metadata.
2. Keep app, timestamps, event type, duration, opaque references, safe browser hostname/surface, structural action and numeric engagement metrics.
3. Redact person, organization, handle and sensitive identifier spans in the surviving window titles, target labels, page surface and allowed action strings; withhold captions containing clinical/patient cues.
4. **Drop** email/chat preview fields, message/body/snippet text, descriptions, unknown metadata, raw URL paths, query strings, user-authored text inputs, and unapproved resource locators.
5. Continue to redact again at the hosted MCP presentation layer; keep the canonical local database untouched.
6. **Fail closed** on projection exceptions; never fall back to sending original local event bytes.

Scope limitation: the independent, explicitly opted-in *cloud work-text* and *visible agent-session message* channels are **not yet fully migrated** to this new projection. They already have distinct local policy/consent, retention and filtering rules, but need separate data-flow audits and gates. Do not claim OWG *never* sends raw names across all features until those channels meet the same standards.

## Rollout sequence and historical data

1. Upgrade desktop connectors (including packaged Mac and Windows launchers). Confirm new Gateway requests contain `metadata.gateway_sync.device_redaction_version == "device-v1"` and `raw_metadata_preserved: false`.
2. Deploy the upgraded Gateway. Legacy clients are still accepted by default for backward compatibility.
3. On an endpoint fleet whose clients have all updated, set **`OWG_REQUIRE_DEVICE_REDACTION=true`** on the Gateway and redeploy. This rejects unmarked legacy structural event uploads (HTTP 400). **Do not switch this on before checking client rollout**, or their sync workers will quarantine the rejected rows under the existing bad-event handling; users could silently lose unsynced work.
4. This marker is not remote attestation: an intentionally malicious endpoint could forge it. The guarantee is against normal code paths, not against compromised capture devices or the Gateway operator.
5. Records uploaded **before** upgrading the desktop or before strict-mode activation may still contain raw titles and metadata. The new code **does not retroactively sanitize or delete historical Gateway rows**, including backups. For confidential/clinical deployments, inventory retention and backups, then plan a controlled organization/actor scoped purge and re-sync from approved local evidence (where the local record still exists). Obtain explicit administrator approval for destructive purge. Do not claim historical data is clean until the purge and backup policy are verified.
6. Existing retained data is still personal data when it contains HMAC pseudonyms or identifying context. This is pseudonymization, not GDPR anonymization.
7. A privacy-first standard should prevent initial upload of recognized patient context *and* consider exclusion of health-related apps, sites or accounts at the capture and sync-policy level. Single-string clinical cues are not enough to prevent every special-category disclosure.

This implementation does not add or download a desktop language model. It adds an **optional, OFF-by-default local NER hook** via `OWG_LOCAL_NER_MODEL=sv_core_news_sm` or `en_core_web_sm`, loading an already installed model on the device and applying typed PERSON/ORG spans before the deterministic detector. If configured but unavailable or crashing, upload fails closed; it never downloads a model or silently falls back. **Do not enable for regulated deployments before held-out evaluation and licensing/packaging review.** The separate spaCy audit remains evaluation-only, because real-world PII performance, packaging footprint, latency and license compatibility are not yet established. See [spaCy Swedish models](https://spacy.io/models/sv).

## Independent held-out release evaluation

The public synthetic fixture tests are smoke tests, **not** valid release-recall estimates. Developers can edit those fixtures and overfit them.

- Keep at least **100 independently annotated** consented/anonymized workplace strings outside the repository; target **200–500** over time. Include at least **10 clinical/special-category** cases and negative UI controls. Sample by source (inbox/calendar/Slack/desktop/health), not just by model failures.
- Each private JSONL record has a `text` field, a `category` (e.g. `clinical` or `general`), `sensitive_spans` with `{"kind":"PERSON|ORG|HANDLE|CLINICAL","value":"..."}`, and optional `must_preserve` fragments. The private content **must not appear in a public repo, model prompt, workflow artifact, failure log, CI build cache, comment, or code review**. Keep annotations private from the coding agent.
- Predeclared pass criteria: **PERSON recall ≥ 95%; ORG recall ≥ 95%; HANDLE recall = 100%; clinical/health sensitive-span recall = 100%; safe UI preservation ≥ 95%**. These are product acceptance targets, *not* proof of safety. Record category-specific counts, over-redaction rate and bootstrap confidence intervals in a later independent study, especially when the sample is small.
- Run `python -m evals.private_redaction_holdout --holdout /absolute/secure/private.jsonl` only on a trusted isolated test machine; the scoring command emits **aggregate numeric results only**, not sensitive examples. The runner refuses to score a holdout inside the source checkout and fails if categories/samples are insufficient.
- **Do not put the holdout or a decryption key in a normal pull_request GitHub Actions secret**. PR authors can modify workflow scripts and potentially exfiltrate secrets. Use a **separate protected private repository/workflow or an external trusted CI service** controlled by a human reviewer. Fetch the exact candidate commit into a sandbox, run a reviewer-owned evaluator, publish only aggregate pass/fail and a protected GitHub status/check to the PR. Do not reveal per-case misses until the holdout has been retired and replaced.
- No secret holdout dataset or protected CI runner is currently connected/configured by this PR. Ordinary public CI passing does **not** mean these release thresholds were measured.

## Clinical data boundary

Health information is a GDPR special category and may also be subject to medical secrecy and professional regulation. A string such as `Patient: Karl Gustafsson, 54 år` must be treated as higher risk than a generic SaaS UI title, even if the patient's name is replaced with a stable token. See the [European Commission GDPR overview](https://commission.europa.eu/law/law-topic/data-protection/information-individuals_en). Any claim of healthcare/enterprise readiness additionally requires a legal basis, organizational controls, retention/deletion governance, and a real privacy assessment.
