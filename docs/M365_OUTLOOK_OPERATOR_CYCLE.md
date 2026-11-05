# Microsoft Graph operator sync cycle (R3)

This is an **opt-in, three-command** local workflow that composes the existing production Graph delta reader and authenticated OWG append/receipt writer. It is not a background scheduler, token manager, provider checkpoint commit, or proof of real-tenant acceptance.

## 1. Extract for human review

Run `python -m server.m365_outlook_operator_cycle extract --help`. Supply the scoped tenant, mailbox, folder, active grant, exact OWG log confirmation, previous candidate/CSV/verified receipt, journal and handoff directories, successor label, and new output/candidate paths. Explicitly pass `--consent-local-read --confirm-removals-reviewed --consent-delta-read --consent-local-export`. Provide the current session as `OWG_DASHBOARD_SESSION` and a short-lived Microsoft Graph token as `OWG_M365_GRAPH_TOKEN` in the operator environment.

This calls the **existing verified next-round Graph exporter**, which rechecks prior handoff, consent, receipt and authenticated OWG inventory, performs bounded provider reads and creates fresh review artifacts. It **does not append**. Inspect the resulting CSV, source identifiers, timestamps, candidate scope, digest, and removal markers. Review and resolve removals separately; removal markers are not silently imported.

## 2. Append only after review

Run `python -m server.m365_outlook_operator_cycle append --help`. Supply the same scoped identity and grant, exact log ID and confirmation, the newly reviewed candidate/CSV and a **new** private `receipt-*.json` path in their directory. Explicitly pass `--consent-local-read --consent-local-append --confirm-candidate-reviewed`. The current local OWG session is required.

The command delegates to the existing durable append implementation. It writes a create-only `.attempt` marker **before** any POST, checks authenticated destination inventory, and writes the receipt only after verified readback. Reusing an attempt or receipt path is refused. A failed or uncertain POST **must never be blindly retried**: inspect OWG history and reconcile separately. A successful receipt is count-level verification, **not event-level membership attestation**.

## 3. Verify and persist the successor handoff

After the append receipt has been durably persisted, invoke
`python -m server.m365_outlook_operator_cycle handoff --help` separately.
Provide the same `--tenant`, `--mailbox`, `--folder`, `--log-id`,
`--confirm-log-id`, `--grant-file`, the appended `--candidate`,
`--csv-path`, `--receipt-file`, and distinct private `--journal-dir`
and `--handoff-dir`, plus `--successor-label`. Explicitly supply
`--consent-local-read --confirm-removals-reviewed` and the active
`OWG_DASHBOARD_SESSION`. No Graph token is needed for this phase.

This delegates to the existing authenticated `prepare_handoff` verifier:
it checks the current grant and saved-log inventory against the confirmed
receipt, persists the private journal and handoff, and refuses conflicting
existing artifacts. It never makes Graph requests, appends events, or advances
provider checkpoints. The next `extract` invocation uses the resulting
handoff and the previous candidate/CSV/receipt. A successful handoff remains
count-level verification, not event-level proof or automatic sync authority.

## Exit codes and guarantees

- `0`: phase succeeded; extraction still requires human review before append.
- `2`: refused, no success claimed.
- `3`: uncertain append; no automatic retry.

Outputs are minimized JSON status without token, session, raw message or email content. All three phases preserve explicit provider scope, active grant checks, tenant isolation, private artifacts and revocation behavior from their production delegates. No provider checkpoint is advanced and no autonomous cycle runs. The caller must not infer verified business outcomes or source-level event membership.

## Validation and remaining acceptance

Run `python -m pytest tests/test_m365_outlook_operator_cycle.py` and the repository's full required native, browser, scale, accessibility, starter, packaging and release workflows. Unit boundary tests are not evidence of a successful authorized Microsoft 365 sandbox run. Production credential rotation, event-level reconciliation, safe recovery of uncertain POSTs and a recoverable checkpoint lifecycle remain open.
