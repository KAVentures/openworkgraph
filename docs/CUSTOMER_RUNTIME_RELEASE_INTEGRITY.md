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
