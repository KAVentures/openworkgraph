# Microsoft 365 sync readiness

Run `python -m server.m365_outlook_sync_readiness --help`.

This opt-in local operator gate combines three previously independent artifacts:
a private mailbox identity report from `m365_outlook_mailbox_identity`, a
private folder metadata report from `m365_outlook_folder_preflight`, and the
latest durable checkpoint from `m365_outlook_checkpoint`.

Supply the exact tenant/mailbox/folder scope, expected mailbox address, log ID,
current grant, both reports, ledger directory and `--consent-local-read`.
All files must remain in private local storage. The command refuses missing or
mismatched evidence and revocation. Its output includes no Graph cursor,
mailbox address, provider identity or source data.

`operator_review_ready` means the three pieces of local evidence agree
for the configured scope. It does **not** mean provider data is complete,
the Microsoft tenant is verified, OAuth is refreshable, or a new Graph GET
is authorized. No provider calls, source writes, OWG appends or checkpoint
advancement occur. For actual next-round extraction, use the separate
checkpoint-anchored operator command with its own fresh permissions and
authenticated membership check. Existing reports can become stale; this
command does not prove their freshness or replace live provider verification.
