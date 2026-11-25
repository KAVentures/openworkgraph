# Offline integrity check for exported customer runtimes (R17)

The **Export implementation project** flow already creates a deterministic
customer-owned runtime ZIP from an exact reviewed and saved process revision.
Its `workflow.spec.json` contains the approved *process model reference* but
does not itself authorize tool use or execution. Implementers previously had
no generated-file inventory to detect accidental edits when moving the
project between machines or reviewing a proposed release.

This change includes two more files in every generated runtime ZIP:

- `release-manifest.json` — a deterministic inventory of the generated file
  names and their exact SHA-256 bytes, the workflow ID/revision/record hash,
  the workflow-spec SHA-256 and the validated process-model digest.
- `verify_release.py` — a standalone, read-only Python standard-library
  verifier. It runs without installing OWG or an agent SDK, does not load
  the runner, and does not call a source system.

## Customer operator workflow

1. Export and extract into a dedicated directory.
2. Before modifying the generated files, run:

   ```sh
   python -S verify_release.py --root .
   ```

3. The verifier checks the expected generated file set and process identity.
   It rejects changed/missing files, duplicate JSON keys, unsafe paths,
   symbolic links and inconsistent process-spec digest fields.
4. Record the printed **manifest SHA-256 separately** in a trusted release
   ticket, versioned record or another system controlled independently of
   the ZIP. When validating another copy, use:

   ```sh
   python -S verify_release.py --root . --manifest-sha256 <previously-recorded-64-hex-digest>
   ```

5. A clean check means the generated files match *that manifest*. It does
   **not** mean that a customer granted tool permissions, deployed those
   exact bytes or achieved the claimed business outcome. Continue with
   the existing business review, tool-schema/source checks, explicit
   current intent, synthetic tests and independent readbacks.

A missing or edited generated file produces a nonzero exit status, with no
attempt to repair it or execute any generated agent. The verifier reports
which generated file differs but never prints its contents. Unknown extra
files (customer case inputs, credentials, tool mappings and journals) remain
outside the inventory, must be separately reviewed, and do not cause the
generated-file check to fail.

## Trust boundary

The manifest is **not signed** and does not provide supply-chain
authenticity by itself. An attacker who controls both the archive files and
`release-manifest.json` can generate a different, internally consistent
manifest. The separately pinned manifest digest is needed to detect this
replacement, and the party who pins it must trust how they obtained it.
This integrity check also cannot detect a malicious Python interpreter,
tampering after the check, or edits to customer-created mapping files.

This is not a remote runtime attestation, a rollback, an approval, a remote
kill-switch, an independent outcome receipt or a license to replay a business
write. The manifest and verification result explicitly report these
boundaries as false.

No new permissions, connections, retention rules, uploads or background
tasks are introduced. The verifier only reads the extracted project. It
does not write to a company account or source provider.

## Verification

- `tests/test_process_agent_release_integrity.py`: exact workflow and
  generated file hashes, reproducible ZIP, standalone `python -S` CLI,
  modifications and deletions, invalid or duplicate JSON, path traversal,
  symbolic-link refusal, optional digest pinning, and the documented
  unsigned-manifest replacement limitation.
- `tests/test_process_agent_project.py`: existing exported runtime suite
  still runs its synthetic offline shadow/exception tests with the two
  additional files present.
- Full cross-platform, generated starter and packaged CI must pass before
  this capability is merged.


## Compare two generated runtime releases before an upgrade

Extract the original and candidate releases into separate dedicated directories.
Obtain both manifest SHA-256 digests from trusted records outside the archives.
Use a trusted copy of the current verifier (do not execute a verifier supplied
by an untrusted candidate):

```sh
python /trusted/verify_release.py --root /review/baseline \
  --manifest-sha256 BASELINE_PIN \
  --compare-root /review/candidate \
  --compare-manifest-sha256 CANDIDATE_PIN
```

The standard-library-only command verifies both generated-file inventories
against their independently retained pins before producing a JSON comparison.
Missing pins, changed files, inconsistent manifests or a different workflow ID
cause refusal rather than a partial report. The report distinguishes:

- newer or older saved revisions, unchanged revision identity, and reuse of
  the same revision number with different content;
- exact workflow-specification bytes versus canonical process-model changes;
- generated paths added, removed or changed, their relevant SHA-256 digests,
  and unchanged file count.

A change to generator code can therefore be reviewed even when the design
has not changed. An older candidate is flagged as such, not automatically
rejected or deployed. The comparison also works with older compatible
manifest-v1 bundles. It reads only generated inventory files and does not
execute, copy, overwrite or install either runtime. The result is a local
snapshot, not a filesystem lock or a signature. Successful comparison is
not an assertion of dependency compatibility, runtime equivalence,
customer-managed binding safety, remote deployment or business outcomes.
Customer-managed files remain outside the generated inventory; review them
separately. Run synthetic acceptance and the applicable reviewed case,
binding, and provider checks before any upgrade. No execution is authorized.

Tests cover same-design code changes, added/removed paths, upgrades,
rollbacks, revision reuse, missing pins, changed files, forged manifests
and the standalone CLI.


### Inspect changes to the underlying saved design

The comparison also reports added, removed and modified stable activity,
transition, business-rule, decision-table and outcome-expectation identities.
Modified objects list changed top-level field names; the report does not
copy labels, goals, rule expressions, table contents or other model values.
A reordered retained identity list is flagged separately. Entry/terminal
changes and other changed model/contract field names are included. Identity
strings themselves may contain customer information and should be reviewed
before sharing the report.

The structural inventory is bounded and rejects duplicate or malformed
identities. It describes exact JSON changes, not semantic equivalence,
validity of a newly edited design, or whether customer bindings still work.
The spec is read with the existing byte bound and checked against its pinned
digest before interpreting these changes. The single-release verifier also
uses the same verified specification bytes for parsing rather than reopening
the spec after hashing it. The check is not a filesystem lock; use dedicated
directories that are not being modified during review.

## Inspect unopened generated runtime archives

Use a trusted current verifier obtained separately from the archive and the
manifest SHA-256 from an independent trusted record. No extraction is needed:

```sh
python /trusted/verify_release.py --archive runtime.zip --manifest-sha256 MANIFEST_PIN
python /trusted/verify_release.py --archive baseline.zip --manifest-sha256 BASELINE_PIN \\
  --compare-archive candidate.zip --compare-manifest-sha256 CANDIDATE_PIN
```

Archive review requires pins, unlike optional-pin directory inspection. The
verifier accepts a closed generated inventory only: every member must appear
in the pinned manifest except the manifest itself. It refuses extra files,
missing files, duplicate or case-aliased names, unsafe paths, Windows device
names, directory/symlink/special-file entries, conflicting ancestor paths,
encrypted or unsupported compression, oversized archives and members. ZIP
and expanded totals are bounded to 64 MiB, with the existing file/manifest
bounds. No extracted file or candidate code is executed. The ZIP container
hash is reported separately from manifest and generated file hashes; a
repack can change container bytes without changing reviewed file contents.
Comparison uses the same structural and exact file delta engine as directory
review. No signature, actual deployment, customer bindings or dependency
compatibility is verified. Use a trusted verifier, not one shipped in an
unreviewed candidate. This is not a general-purpose safe extraction utility.

Tests exercise deterministic exported ZIPs, standalone CLI, member attacks,
byte budgets, pin mismatches, missing and changed members, and upgrade deltas.
Full generated runtime and platform CI remain required.
