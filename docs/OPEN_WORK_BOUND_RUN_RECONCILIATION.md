# Bound-run historical outcome reconciliation

This additive response extends the existing session-authenticated
`POST /v1/open-work/declarations/human-outcomes/bound-run-evidence-history`
operation. Request fields and explicit review confirmations are unchanged.
The operator must still provide an admitted current case revision/edit token,
signed case record link, imported deployment/run identifiers and a current
operator-confirmed binding. Both case ledgers are read through their existing
HMAC/retention/authorization guards. The route rechecks the source, case,
run fingerprint and binding after the reads. No new provider calls, network
writes, ledger writes or agent permissions are introduced.

The response retains `case_provider_evidence_history` and now also returns
`case_human_outcome_history` and `case_historical_reconciliation`. The
reconciliation gives bounded case-wide provider state, human assessment,
dispute counts and explicit review-attention reasons for missing, disputed,
unknown or contradictory histories. Human corrections are interpreted only
through the existing validated human ledger's latest assessment; provider
disputes through the existing validated operation ledger.

A historical paid receipt is **not** a current paid state, a verified
customer/bank outcome, or evidence that the imported run caused payment.
The association is operator-confirmed, never independently authenticated.
Even a matching paid/met pair returns false for customer outcome and agent
causality. These are separate SQLite histories, not an atomic joint snapshot.
Revocation, expiry, missing source, changed declaration or changed run
must fail closed; the operator should perform a separately authorized fresh
provider verification before any operational decision.

Tests: `pytest -q tests/test_open_work_bound_run_reconciliation.py`.
Full native CI and real provider/customer acceptance remain mandatory before
production acceptance.
