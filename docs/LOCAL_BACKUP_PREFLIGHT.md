# Offline backup restore preflight and archive integrity

OWG has an existing local backup archive and offline restore flow. Before replacing any
local store, operators can now perform a **read-only restore preflight** that inspects
an archive and reports explicit blockers. It does not create a data directory,
acquire/change the collector lock, extract files or move existing data.

## Operator workflow

Stop OpenWorkGraph on the target computer first. Identify the backup archive
to restore and, if it is encrypted, set a passphrase environment variable
in your shell. Avoid putting the passphrase in command history or command
arguments.

```bash
python -m server.local_backup verify /path/to/owg-backup.zip
python -m server.local_backup plan /path/to/owg-backup.zip
python -m server.local_backup plan /path/to/owg-backup.zip --replace
```

For an encrypted backup, add `--passphrase-env OWG_BACKUP_PASSPHRASE`.
Never commit, log or share that environment variable's contents.

The plan returns JSON:
- `verified` confirms **archive integrity**, not complete business correctness;
- `ready`, `blockers` and `requires_replace` explain whether the offline
  restore can proceed without overwriting an existing store;
- `owg_version`, `file_count`, `total_bytes` and `includes_secrets` describe
  the backup, not current live storage;
- `data_target`, `auth_target` and `auth_inside_data` state the target layout;
- `operation: offline_restore_preflight_only` and `writes_performed: false`
  distinguish the plan from execution.

Exit code 0 means ready; 2 means a verified archive has one or more blockers;
1 means invalid/corrupt archive or another safe verification error.

After the plan is ready, run the existing offline restore deliberately:

```bash
python -m server.local_backup restore /path/to/owg-backup.zip --replace
```

`--replace` moves old data aside with a timestamp before importing archive files;
it does **not** erase the previous data. An archive without secrets preserves
this device's existing credential files. Encrypted backups include the
credentials, so their restoration may replace local tokens.

## Security and scope

Verification now checks archive and manifest consistency before extraction:
unique members/paths, integer file counts and sizes, matching total bytes,
SHA-256 hashes and declared file kinds, no symlinks/devices, no archive-path
traversal (including Windows alternate separators and drive notation),
and no unencrypted credential entries. Member hashes are read in bounded
chunks, and ordinary ZIP files are opened from disk rather than copied into
memory. Newly created unencrypted ZIP backups also stream to an exclusive
owner-private destination file instead of building the archive in RAM.
Legacy encrypted backups use in-memory AES-GCM decryption and have an
explicit 2-GiB archive limit; all backups also have bounded file count and
uncompressed size. Use smaller rotation intervals for very large encrypted
stores until streaming encrypted backup format support is available.

**Restore stages before swapping live stores.** The archive is fully extracted
and rehashed in a same-volume temporary folder before any existing data folder
is renamed. A failed rename attempts to roll back all previously moved folders.
The operation is **not a single atomic transaction across filesystems**:
power loss, concurrent processes, or a failed rollback may still require
operator recovery. Keep the archive and any pre-restore / failed-restore
folders; do not run restore while a recorder or another restore is active.
Validate production recovery in a dedicated staging machine first.

No new cloud synchronization, AI access, source-system connection, employee
observation or backup upload is enabled. The archive is local and can contain
sensitive metadata; treat it as sensitive even without the credential folder.

## Validation

`tests/test_local_backup_preflight.py` uses synthetic local SQLite stores
and intentionally corrupted ZIP archives to exercise read-only plans,
running-recorder refusal, replacement semantics, CLI exit codes, duplicate
entries, unsafe paths, forged metadata, symlink entries and byte budgets.
These tests supplement the existing `tests/test_local_backup.py` SQLite /
DuckDB and encrypted round-trip checks. This is not a real-device disaster
recovery certification.
