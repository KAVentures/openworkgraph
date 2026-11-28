# Predecessor PR preservation inventory

Roadmap integration safeguard for #1638 and #1572. This tool is a read-only
first pass over **immutable Git objects**, not proof of functional equivalence.
It is intended for integration operators with an existing complete local clone,
including the original predecessor commits and the candidate under review.

## Prepare the inventory

Pin the exact candidate commit and, for each original PR, its original head
and the historical incremental comparison base (normally its original merge
base with the intended base branch at review time). Do not use the current
main head as a substitute for the original comparison base, and do not
silently recompute the base after consolidating a stacked branch.

Save a JSON document with this shape, replacing the placeholders with full
lowercase commit SHAs:

```json
{
  "format": "openworkgraph.pr-preservation-inventory.v1",
  "candidate": "FULL_CANDIDATE_COMMIT_SHA",
  "sources": [
    {"pr": 1573, "base": "ORIGINAL_COMPARISON_BASE_SHA", "head": "ORIGINAL_HEAD_SHA"}
  ]
}
```

Include all predecessor records and divergent alternatives from the audit
issue; keep this inventory independently of branch names. The tool accepts
up to 200 predecessor records. It refuses missing objects, abbreviated
SHAs, duplicate PR IDs, a non-ancestor base, shallow or promisor repositories
and nonempty legacy graft files. Git replacement refs are disabled. The
operator must establish that the comparison bases and source heads are the
correct historical pins; validating their existence and ancestry cannot
prove that they are the intended pins.

## Generate a worklist

From the repository root:

```sh
python scripts/verify_pr_preservation.py . inventory.json
```

Save stdout to a new appropriately protected report file if needed. No
checkout, fetch, commit, ref update, source retirement or merge occurs.
The report lists each PR's source ancestry and every path changed by its
original incremental tree delta, together with the original base, source
and candidate object IDs, modes and object types. Renames are deliberately
represented as removal plus addition. Filenames are losslessly available
as hex in addition to human-readable text. The report contains paths and
hashes, not source file contents.

| Status | Meaning |
| --- | --- |
| exact | Candidate path has the same object ID, type and mode as the original head. |
| deletion_preserved | Original deleted path is still absent. |
| missing | Source path is absent from candidate. |
| deletion_reintroduced | A path deleted in the original delta has reappeared. |
| reverted_to_base | Candidate path matches the original pre-change entry. |
| changed_requires_review | Candidate path differs from both original head and original base. |

A PR with only exact paths can still lose behavior due to changes elsewhere
in the tree. A reintroduced deletion or changed file can be intentional.
Ancestry can be present while all unique functionality has been overwritten.
An exact squash can lack source ancestry. Consequently the report always
sets functional equivalence, retirement authorization and merge authorization
to false; its counts prioritize human review only.

## Complete the actual integration audit

1. Preserve every source ref and original head, including divergent branches.
2. Review original incremental patches and the candidate's actual replacements.
   Map every unique behavior and test assertion to the candidate. Treat
   unresolved, missing and reverted paths as review work, not as permission
   to discard a predecessor.
3. Review cross-file integration, migrations, auth, privacy, failure handling,
   packaging and test collection even when all individual paths match.
4. Restore omissions through the designated integration branch. Run targeted
   regressions and full exact-head current-main CI, including specialized
   enterprise tests. Track real-provider acceptance separately.
5. Verify the accepted candidate is in main before administrative retirement.
   Preserve original refs and review evidence. Never retire on this report alone.

## Verification

```sh
python -m pytest -q tests/test_verify_pr_preservation.py
```

The targeted tests create real temporary Git repositories and exercise
ancestry with lost changes, squash equivalence without ancestry, all six
path classifications, rename/mode changes, gitlinks, replacement refs,
partial/shallow rejection, malformed pins and CLI input. They do not execute
OWG production behavior or perform the enterprise preservation audit.
