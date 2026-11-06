# Microsoft 365 private checkpoint ledger (experimental)

This is a **separately approved local checkpoint commit** for a completed Microsoft Graph delta candidate. It does not prove full mailbox history, payload equivalence, real-tenant integration, or OWG business outcomes. It does not contact Microsoft Graph, append events, or authorize agent execution.

## Operator workflow

1. Run the existing opt-in delta extract, review the CSV and any removal markers, append with a durable OWG receipt, and persist the verified handoff using the existing operator cycle.
2. Use the separate `server.m365_outlook_checkpoint commit` command with the same tenant, mailbox, folder, grant, candidate, CSV, receipt, journal and handoff directories. Provide `--consent-local-read --confirm-removals-reviewed --confirm-checkpoint-commit` and the exact `--log-id` and `--confirm-log-id`. Set `OWG_DASHBOARD_SESSION` for authenticated local membership. The ledger directory must be a distinct owner-private directory (0700 on POSIX). The original candidate and CSV must also remain private.
3. The command rechecks the live grant, strict delta candidate/cursor and CSV digest, existing reviewed handoff, and fresh authenticated saved-event membership. It writes an exclusive generation file with fsync and directory fsync, or refuses. It never automatically advances the next Graph extraction.
4. For subsequent commits, supply `--expected-previous-candidate-sha256` equal to the latest committed CSV SHA-256. A mismatch fails closed. Use `server.m365_outlook_checkpoint inspect` with the scoped grant and `--consent-local-read` to see the generation without printing the private cursor.

Each entry is append-only, scoped by a SHA-256 of tenant/mailbox/folder and linked to its predecessor. Same-generation concurrent writes are exclusive. If a write fails or process terminates, **inspect** the ledger and reconcile manually; do not blindly retry an uncertain write. A corrupt, gapped or ambiguous chain refuses reads and new commits.

This milestone deliberately does **not** wire the ledger into automatic extraction. The existing operator cycle and manual confirmed-resume path are unchanged. Real Graph tenant acceptance, production OAuth lifecycle, revocation races after a successful commit, and complete provider synchronization require separate proof. A checkpoint means only that a bounded candidate was locally accepted after the specified verification gates.
