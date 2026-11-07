# Inspect saved backups in the local Privacy tab

This is an additive **read-only recovery review** for OWG's existing local
backup feature. It builds on the offline restore preflight in
[LOCAL_BACKUP_PREFLIGHT.md](LOCAL_BACKUP_PREFLIGHT.md) (draft M7 PR #693).
It does **not** introduce online restoration, automatic replacement, cloud
upload, AI access, credential export or a new permission.

## Operator journey

1. Open the OWG desktop dashboard, choose **Privacy**, and scroll to
   **Back up recorded activity**. Back up the current store using the
   existing **Back up now** action if appropriate. Backups may contain
   private captured work content; store them accordingly.
2. Choose **Refresh list**. The selector shows the most recent (up to 50)
   \`owg-backup-*.zip\` archives already in OWG's configured local
   backup folder. It does not browse arbitrary paths.
3. Select an unencrypted backup and choose **Inspect restore safety**.
   The dashboard asks the local server to verify the archive and returns
   file count, size, OWG version and current restore blockers, such as
   an active recorder or existing data requiring \`--replace\`.
   No archived event/credential contents or raw files are returned.
4. If an archive is encrypted, the dashboard shows CLI-only guidance
   **without sending a passphrase**, and does not invoke inspection.
   Stop OWG and use:
   \`\`\`bash
   python -m server.local_backup plan /path/to/owg-backup.zip --passphrase-env OWG_BACKUP_PASSPHRASE --replace
   \`\`\`
   Set the passphrase as an environment variable in your shell without
   committing or logging it. The dashboard's optional passphrase field
   belongs to **creating** encrypted archives; it is not used for
   inspection.
5. For an actual restore, **close OWG**, rerun offline preflight, and use
   the existing \`python -m server.local_backup restore\` CLI only after
   resolving blockers. Do not treat a browser report while OWG is running
   as permission to perform a live restore.

The result explicitly states whether the archive verified, why it is
blocked, and that no files were changed. Changing the selector invalidates
the previous result. An in-flight result for another selection is
discarded. Browser text uses \`textContent\` rather than interpreting
backup names or error messages as HTML.

## Endpoint and boundaries

\`POST /v1/local-backups/preflight\` accepts exactly one JSON field:

\`\`\`json
{"name": "owg-backup-20261107T010203000000Z.zip"}
\`\`\`

- Requires a **valid local dashboard session**; the local AI/MCP bearer
  token cannot call it.
- The file must already be in the bounded, existing local backup list.
  Relative/absolute paths, separators, extra request fields, encrypted
  archives and unlisted files are refused.
- Returns \`Cache-Control: no-store\` and a JSON plan from the existing
  offline verifier. No restore or data-mutation operation is exposed
  through HTTP. The request has a 512-byte limit and carries no secret.
- An encrypted archive cannot be reviewed through HTTP even if an
  attacker includes a passphrase field.

This is an operator convenience, not a cross-platform disaster-recovery
certification. An integrity check cannot prove that a source-system
account, business process or customer outcome will be restored.

## Testing

\`tests/test_local_backup_preflight_routes.py\` exercises the real app
with an isolated local session and archive store. It checks dashboard
authorization, bearer denial, exact selection, path/shape refusal,
encrypted CLI-only refusal and that the original store is not moved.
\`tests/browser/backup_preflight.cjs\` is a standalone Chromium
acceptance scenario for create/list/inspect, encrypted backup refusal,
late-response invalidation, WCAG audit and mobile width. Run it with
the same Playwright, axe-core and Python dependencies as the repository's
other \`tests/browser\` scripts.

Both suites and native OS checks must pass on the **exact PR head** before
merging; merely shipping test source is not a passed browser test.
