# Microsoft 365 incremental operator review (R3/R20)

`python -m server.m365_next_round_review` is an **opt-in, non-mutating**
operator entrypoint for a subsequent Microsoft Graph delta round after a
previous generation was durably committed. It composes the production
checkpoint, authenticated OWG event-membership, bounded Graph GET, private
candidate review and transaction preflight adapters. It does not automatically
append events, advance a checkpoint, approve removal markers or retry an
uncertain outcome.

Supply `--tenant`, `--mailbox`, `--folder`, `--log-id`,
`--confirm-log-id`, `--grant-file`, `--previous-candidate`,
`--previous-csv`, `--receipt-file`, `--journal-dir`,
`--handoff-dir`, `--ledger-dir`, `--successor-label`, `--output`,
`--candidate`, `--expected-generation` and
`--expected-previous-candidate-sha256`. Explicitly provide all five flags:
`--consent-local-read`, `--consent-delta-read`,
`--consent-local-export`, `--confirm-previous-removals-reviewed` and
`--require-event-membership`. The Graph bearer is read only from
`OWG_M365_GRAPH_TOKEN`; the authenticated OWG session is read only from
`OWG_DASHBOARD_SESSION`. Do not put credentials on the command line.
The private grant, ledger, output and audit directories must satisfy the
existing production owner-only validation rules.

The operator must inspect the private candidate and removal markers, compare
the returned CSV SHA-256 with the exact reviewed artifact, and then invoke
`python -m server.m365_outlook_reviewed_transaction` separately with fresh
append, removal and checkpoint approvals. No preparation receipt constitutes
authorization to commit. A rejected or uncertain round must be manually
reconciled; never automatically repeat a Graph export against the same output
paths or retry an uncertain OWG append. For audit receipts, configure
`OWG_M365_AUDIT_DIR` on supported POSIX systems.

This is a real adapter/CLI integration, **not yet dashboard integration**.
The first-generation bootstrap remains separate. Provider tenant acceptance,
OAuth refresh/revocation behavior and real-tenant validation remain unverified.
