# Microsoft Graph operator checkpoint commit

This is a **local, explicit checkpoint decision**, not automatic provider synchronization. The command stores the complete validated Graph delta continuation in a new owner-private JSON file only after checking the current scoped grant, the unchanged source candidate/CSV, the verified OWG append receipt, removal-review consent and current authenticated OWG inventory.

Run after a reviewed, persisted append receipt:

```sh
python -m server.m365_outlook_checkpoint_commit \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER \
  --grant-file /private/grant.json \
  --candidate /private/round.json --csv-path /private/round.csv \
  --receipt-file /private/receipt-a.json \
  --checkpoint-file /private/checkpoint-a.json \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --consent-local-read --confirm-removals-reviewed --consent-checkpoint-commit
```

Provide `OWG_DASHBOARD_SESSION` through the existing local session mechanism. The command does **not** call Microsoft Graph, append OWG events, delete messages, update a provider cursor or expose the cursor in stdout. The checkpoint file contains a sensitive Graph continuation URL; keep the directory owner-private (0700), file private (0600), and exclude it from logs, source control and cloud backup. Existing checkpoint files and symlinks are refused rather than overwritten. An uncertain local write must be inspected manually.

The saved cursor is a **reviewed local checkpoint artifact**. It is not yet wired as an automatic resume source: the existing next-round flow continues to validate its previous candidate and persisted handoff. No individual event-membership proof, cross-process global checkpoint ordering, live Microsoft Graph sandbox acceptance or production credential lifecycle is claimed. A revoked grant blocks new commits; it does not retroactively erase a previously committed private file. Run `pytest -q tests/test_m365_outlook_checkpoint_commit.py` for focused checks, and require the full native CI gates before merging.
