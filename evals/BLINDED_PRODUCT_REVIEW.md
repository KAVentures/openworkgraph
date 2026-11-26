# Blinded product-value pilot review

This offline operator workflow supports the existing three-arm product-value
study in [README.md](README.md). It is not a model runner and does not certify
an actual trial, reviewer independence, or successful blinding.

## Prepare the real pilot

Freeze independently verified episode truth using the continuity schema.
Run the three matched conditions with identical goals, model settings and
source snapshots, preserving actual transport traces privately. Retain only
authorized, manually minimized material.

For each arm supply one JSONL row per episode:

```json
{"episode_id":"case-a","recorded_model_completion":true,"model_id":"same-model","source_state_id":"same-frozen-state","review_material_approved":true,"review_text":"Approved neutral account of the agent response, selected opaque resources, relevant independently verified source-state changes and clarification turns."}
```

This is a format example, not an observation. Use the same evaluation ID
namespace for all evidence, including ground truth. Remove arm names, tool
branding, OWG-specific call names, run metadata and other condition clues from
the prompt and review text where this can be done without changing the
substantive evidence. Include enough independent source-state evidence for
the reviewer to determine success and consequential mistakes. If the source
evidence is insufficient, resolve it before preparation; never invent outcomes.
A human operator must approve every row and explicitly confirm the export.
Free text may still reveal a condition; document any failed blinding.

From the repository:

```sh
python -m evals.blinded_product_review prepare \
  --episodes /private/truth.jsonl \
  --control /private/control.jsonl --raw /private/raw.jsonl \
  --goal /private/goal.jsonl \
  --review-dir /private/reviewer-packet \
  --private-dir /private/operator-key \
  --confirm-reviewed-material
```

The directories must be new, separate and non-nested. The operator key maps
random review IDs back to arm, episode, model and frozen source state. Keep
it and the original transport/ground truth in a private operator location.
The reviewer packet contains only opaque randomized IDs, the approved prompt
and review text, and a blank JSON review template. Inspect the final packet
before sharing, and share only the reviewer directory through an authorized
channel. This program performs no network operations and does not enforce
recipient access controls on the operator's filesystem. Hashes detect
accidental changes relative to a trusted private key; they are not signatures.
If preparation fails partway through, inspect and use new output paths.

## Independent review

Ask a reviewer other than the ground-truth verifier to fill each template row.
Record selected resource and agent-run IDs actually relied on; count
clarification questions and unsupported assumptions; judge completion and
consequential wrong actions from the authorized evidence. Do not guess.
Save the completed JSON array separately. Do not send the operator key or
arm-labeled source traces to the reviewer. Retain reviewer identity and any
blinding limitations outside the public repository.

## Reconcile and score

```sh
python -m evals.blinded_product_review score \
  --episodes /private/truth.jsonl \
  --packet /private/reviewer-packet/packet.json \
  --key /private/operator-key/key.json \
  --reviews /private/completed-review.json \
  --out /private/pilot-report.json
```

The scorer refuses changed truth or packet, missing/duplicate/unknown review
IDs, incomplete reviews and mismatched paired metadata. It delegates to the
existing canonical three-arm report and paired statistics, preserving all
original decision gates. Synthetic examples remain pilot-only. No existing
runtime API, recorder setting, or scorer is modified.

Keep all real private study data outside version control. A successful report
does not establish representative customer outcomes or causal lift without a
proper matched study.

```sh
python -m unittest discover -s tests -p test_blinded_product_review.py
```

For two independent reviewers and third-reviewer adjudication of all differing entries, see [MULTI_REVIEWER_PRODUCT_REVIEW.md](MULTI_REVIEWER_PRODUCT_REVIEW.md).
