# Offline provider-attempt receipt management (experimental)

The operator can inspect one attempt's bounded local verification history and explicitly
apply retention or revocation deletion without connecting to Stripe. This is **not**
an authenticated REST endpoint and does not grant access to another actor's data.
The operator must already be authorized to read the local journal file and act
for the supplied tenant and actor. Store the journal in an access-controlled directory.

Run from the repository root:

```sh
python -m server.open_work_attempt_receipts_cli --journal-path /secure/receipts.sqlite3 --tenant org1 --actor operator1 history --attempt run1
python -m server.open_work_attempt_receipts_cli --journal-path /secure/receipts.sqlite3 --tenant org1 --actor operator1 retention --older-than-days 30 --confirm-delete
python -m server.open_work_attempt_receipts_cli --journal-path /secure/receipts.sqlite3 --tenant org1 --actor operator1 purge --confirm-delete
```

History is capped at 100 latest entries for an exact attempt; each receipt records
the independent provider-event verification verdict and retains unknown customer
outcome. Retention deletes only entries strictly older than the requested window,
scoped to one actor and tenant. Purge removes all that actor's receipts in the
specified tenant, for example following a separately authorized revocation.
Neither operation is automatic. The explicit confirmation flag is mandatory for
deletion. No provider credentials, network requests, or business writes are used.

**Limitations:** The SQLite journal is local experimental storage, not an
organization-wide authorization system or a signed source-of-truth ledger.
The CLI does not check grants, revoke tokens, enforce scheduled retention,
validate a real Stripe account, or prove provider-operation/customer success.
A production API must independently enforce current consent, tenant/actor
authorization, retention and revocation before exposing these operations.
