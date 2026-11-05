# Operator-bound run evidence receipt journal (experimental)

This is an **optional dashboard-session-only** API. It records that an
authenticated operator reviewed retained case-wide evidence in the context
of a current explicitly confirmed imported-run/case binding. It does **not**
prove the provider action was performed by that run, that the agent caused
the outcome, or that a customer/business outcome was completed.

POST `/v1/open-work/declarations/human-outcomes/bound-run-receipt-journal`
uses the same authenticated session and case/run admission fields as
`bound-run-evidence-history`: `work_item_ref`, `expected_revision`,
`expected_edit_token`, `max_cases` (1–4), `deployment_id`, `run_id`,
`record_link`, `confirm_historical_review: true`, and
`confirm_run_comparison: true`.

Without an `action`, the endpoint reads the current private receipt journal.
For an explicit write, add `confirm_receipt_write: true` and
`action: "reviewed"` or `"disputed"`. Corrections specify `supersedes`
with the previous receipt ID. Retraction uses `action: "retracted"` and
requires `supersedes`. The server hashes the current HMAC-validated case
provider and human histories itself; it does not accept arbitrary source
claims or customer information from the client.

The journal is stored locally with HMAC integrity, actor/scope/case/run
partitioning, a bounded 256-row per-run read, and expiry no longer than
the configured human-history retention (maximum seven days). Changed run
fingerprint or declaration revision makes prior receipts non-current.
Source disconnect/replacement purges the old scope's journal. Revoking
the operator binding denies the authenticated route; it does not confer
independent provider evidence authority. A write is not automatic, and
no provider network call, source write or agent execution occurs.

**Acceptance boundaries:** The local tests exercise durable correction,
retraction, actor isolation, retention, HMAC tamper rejection and source
purge. Full native CI and real-provider/customer acceptance must be
separately verified before merging or claiming outcome verification.
