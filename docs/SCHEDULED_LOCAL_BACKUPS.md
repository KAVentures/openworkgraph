# Scheduled local backups — owner opt-in

This adds a real recurring **local-only** backup job using OWG's existing
opt-in scheduler; no extra daemon or startup capture behavior. It is part of
the M7/R20 product-reliability work and stacks on the offline restore
preflight (#693) and Privacy recovery-inspection UI (#694).

## Use

Open the local OWG dashboard, choose **Privacy → Scheduled jobs**, then:

1. Choose **Local backup (unencrypted)**. The target is automatically set to
   **This computer**; no remote source, source-system identity, tenant, or
   path selector is accepted.
2. Leave the daily interval at 1,440 minutes or choose longer, up to 7 days.
3. Explicitly tick the separate acknowledgement: scheduled backups are **not
   encrypted** and can contain private recorded work, messages, captured
   interaction detail, screenshots and other evidence if present. Credentials
   in OWG's configured auth directory are excluded.
4. Confirm the additional scheduling prompt. Creating the job **does not
   immediately create a backup**. The next due run will produce and verify
   an archive in OWG's configured local backup folder. Pausing or deleting
   the schedule stops future scheduled runs.
5. Use **Privacy → Back up recorded activity → Inspect restore safety** to
   inspect locally saved archives. Restoring data still requires fully
   closing OWG and using the offline CLI.

Existing Jira/source and event-log signal schedules are unchanged. A
separate running-recorder pause check continues to control provider reads;
local snapshots do not require those source-read permissions and never
trigger new external data collection.

## Backup file lifecycle

Scheduled archives use names like
`owg-backup-scheduled-20261107T010203123456000000Z.zip`.
Each run snapshots via OWG's existing local backup creator. Only after the
new archive passes full manifest/file-digest verification does the scheduler
record its filename, byte size and modification time in the private on-disk
`.owg-scheduled-backups-v1.json` ownership ledger. Rotation uses this ledger
and removes only unchanged owned archives, keeping the newest 14. Manual
archives are never deleted, **even if a user named one to look like a scheduled
archive**. Replaced, changed and symlinked files are not deleted, and a
malformed or symlinked ledger makes the run fail closed. A crash between
successful archive writing and ledger commit can leave an untracked archive
which OWG never auto-deletes; the operator can inspect and remove it manually.
Automatic rotation does not erase separately stored copies.

There is only **one scheduled local backup job** per OWG local scheduler.
Runs are serialized by its existing lock. Failures use the existing safe
backoff/retry counters and pause after five consecutive failures. Job
history contains fixed, content-free outcomes; it does not contain file
names, private paths, work content or passphrases.

The job does **not** provide encrypted unattended backup: keeping a
decryption secret on disk or in schedule JSON would weaken the privacy
boundary. For encrypted archives including keys use the manual
passphrase-based flow, and arrange secure storage according to your own
organization's requirements.

The local backup folder must be outside both the data and credential
folders. An invalid folder is refused at creation time. If that path changes
after a valid schedule is saved, the job stays configured but its due run
fails with a fixed, content-free status and ordinary backoff; it is **not**
silently removed as a missing source target. Correct the path and resume
if it has paused after five failures. Archive creation and verification
can fail due to disk, permission or corruption; on failure, retention does
not run. This is not remote/off-site disaster recovery,
a guarantee of filesystem crash consistency across all applications,
or proof that real business outcomes can be reconstructed.

## API

The existing authenticated local dashboard route is extended, with no
new authorization mechanism:

```json
{
  "kind": "local_backup",
  "target_id": "local",
  "interval_minutes": 1440,
  "confirm_scheduled_runs": true,
  "confirm_unencrypted_local_backup": true
}
```

Submit to `POST /v1/schedules` **only with a valid local dashboard
session**. The additional acknowledgement must literally be JSON
`true`. Sending it as a string or omitting it fails. Minimum backup
interval is a full day; updating an existing backup schedule to a shorter
interval also fails. A local AI/MCP bearer cannot create or change schedules.

`GET /v1/schedules` shows the owner the local schedule and its
unencrypted-backup acknowledgement. The existing pause/resume and remove
routes work unchanged. Neither this route nor the job grants AI access,
company synchronization, connector reads, external delivery or business
execution.

## Validation

`tests/test_local_scheduler_backups.py` covers literal opt-in, cadence,
fixed local target, no eager execution, actual scheduled snapshot and SHA
verification, no leaked keys, isolated rotation (including symlinks, manual lookalikes, tampered archives and a durable
ownership ledger), unsafe folder refusal and failure diagnostics/backoff.

`tests/test_local_scheduler_backup_routes.py` exercises a real local
ASGI router in a subprocess with an actual dashboard session and
bearer-token denial. `tests/browser/scheduled_local_backup.cjs` runs
the Privacy opt-in through authenticated Chromium, including failure
and success paths, pause/resume/delete, WCAG and narrow mobile width.
Synthetic data only. The separate path-filtered Chromium workflow is
extended to execute this browser test; existing CI jobs are not changed.

Exact-head native and browser CI must pass before merge. Full product
parity and a real-device recovery drill remain separate acceptance gates.
