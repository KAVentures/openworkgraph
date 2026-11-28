# Set up a reviewed shadow skill in an isolated Claude Code project (P0 #1663)

This local command complements the native shadow package renderer (#1670),
dashboard download (#1678), and direct proposal-to-skill handoff (#1679).
It performs an explicitly requested local project file operation, not a
model invocation, a tool grant, or production deployment.

## Before setup

Create an isolated test project with synthetic data or separately
authorized read-only fixtures. Independently configure the actual
client's tool restrictions and verify that no real business writes,
sends, spending or live deployment are possible. Close the client and
ensure exclusive local control of the package and test project while
running these commands. This utility is not a sandbox or protection
against concurrent malicious filesystem mutation.

Extract the trusted, human-reviewed package into a separate directory.
Retain its original package SHA-256 from the review, not merely a value
read back from an untrusted archive. Inspect the complete instructions
and source/tool mapping. A digest is not a signature or proof of safety.
Do not install packages from unknown authors.

From the OWG repository:

```sh
python -m shared.ai_skill_project_install install \
  --package extracted-skill --package-sha256 EXACT_PACKAGE_PIN \
  --project isolated-test-project --confirm-isolated-project --confirm-change
```

The existing package verifier checks the complete extracted package.
The installer also requires a shadow-only manifest, exact proposal
lineage, manual-invocation front matter and no known dynamic shell
injection syntax. It creates only the necessary project-local directories
and exact .claude/skills/<name>/SKILL.md, refusing an existing skill
directory even if its contents are identical. It does not write settings,
hooks, tool permissions, other project files or the global skill directory.
It refuses symlinked project roots and skill path components, and
overlapping package/project directory trees. Parent path resolution and
ordinary filesystem access require a trusted, quiescent local environment.

## Review and remove

```sh
python -m shared.ai_skill_project_install review \
  --package extracted-skill --package-sha256 EXACT_PACKAGE_PIN \
  --project isolated-test-project --confirm-isolated-project
```

This is read-only and checks the exact installed skill bytes and directory
inventory, not Claude Code's discovery or use of the skill. Independently
check the installed client/version and permissions, then invoke the skill
manually using the supported client UI and run the frozen shadow cases.
Use the existing agent trace and source-check review workflows to evaluate
observed activity and independent source evidence.

To explicitly remove the matching installed shadow skill, first close the
client, then run:

```sh
python -m shared.ai_skill_project_install remove \
  --package extracted-skill --package-sha256 EXACT_PACKAGE_PIN \
  --project isolated-test-project --confirm-isolated-project --confirm-change
```

Removal refuses changed bytes, unexpected entries or symlinks. It deletes
only the exact matching SKILL.md and its now-empty skill directory,
never recursively removes a project or other skills. The command checks
content, not who initially installed it, so confirm that this is the
specific skill you want removed. Any interrupted or partially failed
operation requires manual inspection; no automatic cleanup is attempted.
Removal does not undo external side effects or erase prior client context.

All three operations print a minimized, digest-pinned receipt with the
package, proposal, evidence and skill identifiers and the relative path.
The receipt explicitly states that isolation, discovery, invocation,
permissions, deployment and business outcomes have not been verified.
No receipt grants authorization. Keep originals and receipts under the
customer's own retention policy.

## Validation

```sh
python -m pytest -q tests/test_ai_skill_project_install.py
```

The tests exercise real local directories, exact copy and removal,
preservation of unrelated project files, existing-directory refusal,
changed file and extra-content refusal, symlinks, invalid pins and
lineage, overlapping directories, non-shadow mode, known shell injection
syntax and explicit CLI confirmation. Installed-client and full repository
acceptance remain separate gates.
