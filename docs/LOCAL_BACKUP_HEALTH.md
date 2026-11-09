# M7/R20 — Local backup integrity health

This adds an **explicit read-only integrity inventory** for existing OWG
backups. It builds on the local backup creation/listing and offline restore
preflight in #693/#694. It is not a restore operation.

## Owner workflow

In the local dashboard, open **Privacy → Backups**, then select
**Verify first 5 listed backups**. The owner confirms the read. OWG checks
at most five saved local ZIP archives already in the configured backup
folder, without uploading them or sending credentials anywhere.

For each selected archive, the results show its basename and one of:

- **Verified intact at this check:** every archived plaintext file matched
  its saved byte count and SHA-256 digest in the manifest, and the
  selected file was still unchanged when the scan ended.
- **Integrity check failed:** a manifest/member/hash/ZIP validation failed.
  Do not rely on this archive for recovery until investigated.
- **Encrypted — verify using offline CLI:** no password is ever requested
  or transmitted by the dashboard.
- **Too large/budget exceeded:** the dashboard deliberately did not
  fully verify this archive. Use the offline CLI.
- **Unavailable/non-regular/changed during check:** a saved file
  disappeared, was a symbolic link, was inaccessible or was modified
  while checking.

The UI displays counts of verified and unverified selected archives and
the number omitted by the five-item list budget. An old request result
cannot replace a newer refreshed list. Reported statuses are text only,
never raw HTML, and cannot contain source contents or absolute file paths.

## Protocol

`POST /v1/local-backups/health`, authenticated by the **existing local
human dashboard session**. An AI/MCP bearer token is rejected.

```json
{
  "names": ["owg-backup-example.zip"],
  "confirm_local_verification": true
}
```

Exactly 1–5 **distinct already-listed** basenames are required. The
request must be JSON and at most 1,600 bytes. Paths, extra fields,
duplicate names, passphrases and unknown files are refused.

The response
(`openworkgraph.local-backup-health.v1`) is `Cache-Control: no-store`
and reports the verification timestamp, per-file status,
verified/not-verified counts, archive sizes, and OWG version/file count
only for files verified intact. It deliberately excludes archive
contents, member names, credentials, absolute paths, source data and
agent action authority.

The browser operation is capped at **1 GiB per archive**, **2 GiB of
selected archive input per check**, and **2 GiB of declared uncompressed
ZIP content** to avoid excessive expansion. Larger archives can still be
backed up and inspected offline; exceeding the dashboard budget
does not mean an archive is corrupt. The existing archive reader
streams plaintext ZIP entries and verifies the same versioned manifest
as the CLI. A file signature is rechecked after reading to reject
changed/replaced files.

## What this does and does not prove

**Verified** establishes local integrity of a particular saved ZIP
at the verification time. It does not prove the source was complete,
that live capture is healthy, that an external service is available,
that the backup includes credential keys, that retention is sufficient,
or that restore will succeed. For disaster-recovery assurance, test
the **offline** restore against an isolated, authorized environment
and keep a known-good copy elsewhere under your own security policy.
No restore, staging, deletion, rotation, passphrase handling or business
write is possible through this endpoint.

Scheduled unencrypted backups introduced separately in #696 may be
listed and checked if they meet these limits. Their existence, even
with a recent timestamp, does not certify an entire recording or
enterprise connector's data quality.

## Acceptance

- `tests/test_local_backup_health.py` exercises real plaintext archive
  creation/verification and nonmutation, forged damaged entries,
  encrypted backups, symbolic links, oversized sparse files, strict
  request validation, race against archive mutation and an actual
  ASGI route requiring dashboard session and refusing AI bearer.
- `tests/browser/backup_health.cjs` covers real Chromium Privacy:
  create/list, explicit bounded verification, encrypted CLI-only,
  synthetic tampering, stale-response suppression, no restorer,
  WCAG and mobile layout.
- `.github/workflows/local-backup-health-browser.yml` runs focused
  backend and Chromium/axe acceptance only when this feature's paths
  change; existing global CI remains unchanged.

This is a **draft R20 operational-readiness slice**, not certification
of production backups, disaster recovery, or full competitor parity.
The parent #693/#694 restore-safety PRs must merge after exact-head
tests before this dependent feature can be integrated.
