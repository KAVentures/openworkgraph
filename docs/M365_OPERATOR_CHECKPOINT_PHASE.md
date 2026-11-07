# Microsoft 365 checkpoint in the operator cycle

The existing `python -m server.m365_outlook_operator_cycle` now supports two explicit phases:

- `checkpoint-inspect --tenant T --mailbox M --folder F --grant-file GRANT --ledger-dir PRIVATE_LEDGER --consent-local-read` reads the current generation, **not** the private provider cursor. It refuses an invalid grant or ledger.
- `checkpoint-commit` takes the same scope, grant and ledger flags plus `--log-id`, `--confirm-log-id`, `--candidate`, `--csv-path`, `--receipt-file`, `--journal-dir`, `--handoff-dir`, `--successor-label`, and the separate `--consent-local-read --confirm-removals-reviewed --confirm-checkpoint-commit` approvals. On subsequent generations supply `--expected-previous-candidate-sha256` equal to the previously committed CSV digest.

Run extract, offline review, append, authenticated handoff and membership verification first as described in [M365_OUTLOOK_CHECKPOINT.md](M365_OUTLOOK_CHECKPOINT.md). The commit delegates to the existing strict checkpoint implementation, which revalidates scope, grant, reviewed handoff and live OWG membership before writing the exclusive generation. `OWG_DASHBOARD_SESSION` is required for the authenticated membership check.

A checkpoint is a *local reviewed cursor*, not a complete provider sync. Neither command requests Microsoft Graph, retries uncertain appends, advances the next extraction automatically or authorizes business actions. An uncertain write must be inspected and manually reconciled; do not blindly retry. Provider sandbox acceptance, production OAuth, retention and revocation race acceptance remain separate gates.
