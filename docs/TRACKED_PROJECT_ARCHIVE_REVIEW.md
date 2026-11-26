# Verify original tracked agent project downloads (R17)

After exporting an agent project from an approved automation opportunity,
open **Verify a previous project export**, select the unchanged original
ZIP (maximum 16 MiB), and click **Verify original project ZIP**.

The review first matches the exact archive SHA-256 against a retained
project belonging to the selected idea. Unmatched bytes are never opened.
It then reads the ZIP in memory, checks the opportunity lineage and exact
specification against the saved export metadata, and reconstructs the
generated-file manifest using every generated member. Duplicate names,
unsupported member types, excess expanded size (64 MiB), and inconsistent
inventories fail closed. No archive is extracted, executed or saved.
Historical exports without a generated-file manifest are explicitly
identified as such; the original outer archive digest still matches.

The report separates historical export identity from the current idea
revision and saved design status. It provides the original manifest digest
for independent pinning. No base ZIP is reconstructed; the base archive
hash is checked as a retained lineage declaration. No independent signature,
remote deployment, execution, or business outcome is established.

The local dashboard-session-only endpoint POST
`/v1/automation-pipeline/{idea_id}/project-archive-review` accepts raw
`application/zip` bytes, returns a minimized no-store JSON report, and
does not persist a review. The original ZIP must not be edited or repacked;
for customer-modified extracted directories use the separately trusted
release verifier with an independently pinned manifest digest.

These checks rely on the integrity of local project records and the trusted
OWG application. They do not authenticate an attacker-controlled replacement
of both the local database and the archive. No provider or model calls occur.

Regression coverage: exact original export, altered and cross-idea ZIP
refusal before parsing, inconsistent inner inventory, historical revision
drift, authenticated HTTP and dashboard file selection.
