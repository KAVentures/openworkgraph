# Salesforce CaseHistory: reviewed incremental checkpoint

Scope: [#326 Section B](https://github.com/KAVentures/openworkgraph/issues/326), [#264 Stage 3](https://github.com/KAVentures/openworkgraph/issues/264), [#370 R3](https://github.com/KAVentures/openworkgraph/issues/370). This adds a **two-phase, operator-controlled incremental workflow** on top of the existing bounded live CaseHistory reader. It is not an unattended scheduler, a Salesforce sandbox certification, an OWG import API, or a provider-authoritative completeness guarantee.

## Prepare → review/import → commit

Use a private, owner-only directory on the OWG host (POSIX mode 0700), with a current approved **read-only** Salesforce bearer in `OWG_SALESFORCE_READ_TOKEN`. Confirm the exact instance hostname, stable organization namespace and 1–8 selected literal Case IDs. The bootstrap lower bound and the first upper bound must be explicit second-precision UTC timestamps within 31 days. The example uses placeholders, not live credentials.

```sh
mkdir -m 700 /private/owg-salesforce
export OWG_SALESFORCE_READ_TOKEN='<current-approved-read-only-token>'
python -m server.salesforce_case_history_incremental prepare \
  --instance https://example.my.salesforce.com \
  --confirm-host example.my.salesforce.com \
  --namespace approved-sandbox \
  --case 500000000000001 \
  --bootstrap-since 2026-10-01T00:00:00Z \
  --until 2026-10-03T00:00:00Z \
  --overlap-hours 24 \
  --checkpoint /private/owg-salesforce/checkpoint.json \
  --manifest /private/owg-salesforce/review-001.json \
  --output /private/owg-salesforce/history-001.csv \
  --consent-selected-read --consent-private-export
```

`prepare` performs the same one-request, at-most-200-row, fixed-field Salesforce CaseHistory GET as the live reader. It creates a **new** owner-only CSV when rows exist and a **new** owner-only JSON review manifest, but **never advances the checkpoint**. It returns counts, `manifest_sha256` and `scope_sha256` without raw IDs, credentials or source values. Review the scope, time window, output, counts and any import warnings. Import the CSV explicitly into OWG **Event logs → Import CSV**, mapping `case_id`, `activity`, `timestamp` and `source_event_id` (to preserve duplicate suppression across overlapping windows). Import to the same intended log and verify the local import succeeded before committing. A successful CLI extraction is not evidence of a successful OWG import.

Only after an operator has independently verified the intended import should they attest the manifest digest printed by `prepare`:

```sh
python -m server.salesforce_case_history_incremental commit \
  --checkpoint /private/owg-salesforce/checkpoint.json \
  --manifest /private/owg-salesforce/review-001.json \
  --confirm-manifest-sha256 '<64-hex-sha256-from-prepare>' \
  --confirm-imported
```

If `prepare` returns `csv_created: false`, it creates **no CSV**. After explicitly reviewing that empty window, use `--confirm-empty-window` **instead of** `--confirm-imported`. An empty source response does **not** imply no source changes existed or that a grant covered every history row.

For the next run, choose a strictly later `--until`, a new manifest filename and a new CSV filename; omit `--bootstrap-since` (or repeat the identical original bound). Keep the **same** checkpoint, namespace, selected cases, confirmed instance and overlap. The next lower bound is `max(original_bootstrap, previous_until - overlap_hours)`. Replayed `source_event_id` values are expected and must be deduplicated by OWG's import/append workflow. A window with more than 200 rows or incomplete pagination fails closed; shorten the requested upper bound and retry with fresh output names. Never advance a checkpoint for a refused extraction.

## Crash, concurrency, privacy and scope

The checkpoint is a local owner-only canonical JSON file. Commit checks the exact operator-supplied manifest SHA-256, CSV SHA-256, source-scope fingerprint and expected prior checkpoint digest. It takes an exclusive local lock, rechecks the current checkpoint and writes a replacement atomically; a competing/stale review cannot silently advance it. If a crash leaves a `.lock` file, **investigate the checkpoint and import status first**; do not automatically delete the lock or blindly replay a commit. A local administrator controlling the files and confirmations is trusted: SHA-256 is an integrity/review guard, not cryptographic proof of import, external authorization or protection against a malicious local administrator.

Do not commit until the actual OWG import has been checked. In particular, `--confirm-imported` is an **operator attestation**, not an OWG-issued import receipt. An operator can make a false attestation; this workflow cannot detect it. There is no centralized token vault, revocation feed, source deletion handling, retention scheduler or automatic provider/OWG import. Source retention and row-level permissions may omit historical changes; late observations older than the chosen overlap may be missed. Overlap is bounded to 1–168 hours, not a promise of source completeness. No external business writes occur. Protect and delete the private CSV, manifest and checkpoint under the organization's retention policy. Never put source credentials in flags or files.

## Verification and rollout gate

`tests/test_salesforce_case_history_incremental.py` exercises a disposable **real TCP Salesforce-shaped HTTP server** through the existing production live extractor, a private CSV, checkpoint prepare/commit/resume with overlap, stale competing reviews, digest tampering, explicit empty-window decisions, consent and scope refusal. Synthetic provider fixtures do not establish real Salesforce customer acceptance. Before merge, require exact-head native Ubuntu/macOS/Windows pytest, browser, process collaboration, scale, accessibility, starter, packaging and release checks, resolved review threads and a current conflict-free main. An authorized Salesforce sandbox and human-reviewed OWG import remain separate deployment acceptance.
