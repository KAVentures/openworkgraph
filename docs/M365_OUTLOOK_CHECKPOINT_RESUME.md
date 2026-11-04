# Microsoft 365: resume extraction from an operator-committed checkpoint

This is an **explicit, bounded read-only Graph delta round**, not a background sync.
First complete the documented operator extraction, review, authenticated OWG
append and checkpoint commit steps. Keep the original private candidate CSV,
candidate JSON and committed checkpoint together in a mode-0700 directory.

Run from the repository root, with an active exact-folder grant and a
least-privilege Graph bearer in `OWG_M365_GRAPH_TOKEN`:

```bash
python -m server.m365_outlook_checkpoint_resume \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER_ID \
  --grant-file PRIVATE/grant.json \
  --checkpoint-file PRIVATE/checkpoint-first.json \
  --previous-candidate PRIVATE/first.json \
  --output PRIVATE/second.csv --candidate PRIVATE/second.json \
  --consent-delta-read --consent-local-export --confirm-checkpoint-resume
```

The command verifies current grant and exact scope, a private committed
checkpoint, the immutable SHA-256 of the previous candidate, its validated
Graph delta cursor, and the source CSV hash. It refuses missing consents,
changed inputs, revoked grants and path collisions. It then delegates to
the existing production bounded Graph delta extractor (eight pages and
200 records maximum). New CSV and candidate files must not exist.

**Review the new candidate and removals before a separate authenticated
OWG append.** This command does not append, retry uncertain OWG writes,
advance or overwrite a checkpoint, or grant downstream execution authority.
Provider delta links are bearer-like secrets and are never printed. Do not
put credentials or raw links in logs, issue comments or PRs.

A previous committed checkpoint attests an operator decision and verified
inventory counts, **not** independent event-level membership or successful
real-provider acceptance. For incomplete pages, expired cursors, throttling
or network failure, extraction fails closed and requires operator triage;
do not blindly retry an uncertain append. Real Microsoft sandbox validation,
credential rotation and independent event reconciliation remain separate gates.
