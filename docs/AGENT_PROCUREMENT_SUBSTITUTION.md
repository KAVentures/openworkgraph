# Candidate substitution artifact preflight

After the evidence-pinned selection receipt, connect the selected alternative
to the actual extracted runtime artifact inventories using the existing R17
manifest verification and comparison. This is a local CLI only; the dashboard
does not accept arbitrary filesystem paths.

Provide the exact saved process export, full original decision bundle,
selection receipt, baseline and candidate extracted OWG runtime directories,
and a request containing:

```json
{
  "expected_selection_receipt_sha256": "REPLACE_WITH_SELECTION_RECEIPT_SHA256",
  "baseline": {
    "configuration_sha256": "REPLACE_WITH_FROZEN_BASELINE_CONFIGURATION_SHA256",
    "manifest_sha256": "REPLACE_WITH_PINNED_BASELINE_MANIFEST_SHA256"
  },
  "candidate": {
    "configuration_sha256": "REPLACE_WITH_FROZEN_CANDIDATE_CONFIGURATION_SHA256",
    "manifest_sha256": "REPLACE_WITH_PINNED_CANDIDATE_MANIFEST_SHA256"
  },
  "confirmed": true
}
```

Keep manifest hashes separately from the artifacts. Reuse
PROCESS_SHADOW_EVALUATION.md and the R17 release inventory instructions.
Only the generated file inventories are verified. This requires OWG-generated
runtime artifacts; arbitrary external agent binaries are not supported by
this specific preflight.

```sh
python -m shared.agent_procurement_substitution --process-export process.json --decision-bundle decision-input.json --receipt receipt.json --baseline-directory extracted-baseline --candidate-directory extracted-candidate --request artifact-review.json --out preflight.json
```

OWG recomputes the exact decision from both trial populations, refuses
non-selection dispositions or changed configurations, verifies both generated
file inventories against supplied manifest pins and requires their full
canonical runtime specifications to match the evaluated saved revision.
The existing R17 comparison exposes generated file differences. A different
process revision, stale selection receipt or changed generated file is
refused. Both candidate configurations may use the same generated runtime
artifact; configuration and artifact identity are separate.

The receipt's declared link between the candidate configuration and the
artifact is not an independent execution attestation. The preflight does
not verify customer-managed files, installed dependency versions, live
MCP tool schemas, sandbox behavior, source outcomes or production
authorization. It does not execute or replace either runtime. The remaining
rehearsal prerequisites are explicit in the exported evidence.
