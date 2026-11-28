# Multi-proposal implementation discovery from shared human-work evidence

P0 #1663: one bounded evidence packet can support multiple independent
external-AI workflow proposals, rather than requesting or interpreting the
same human observations repeatedly. The model performs the semantic work.
OWG reuses the existing v1 proposal and structural reviewer, not a second
workflow inference or ranking system.

## Ask an authorized external AI

Prepare the existing original or compact evidence packet. Provide its
matching instructions with this additional request: return a single JSON
array containing one to twenty independent v1 implementation proposal
objects, each obeying the existing proposal format exactly and using the
same original packet pin. Propose only candidates justified by the evidence;
do not invent enough candidates to reach a target. Preserve uncertainty,
alternative implementation routes, observed examples and exceptions.
Each proposal should be useful on its own. No inferred permission, policy,
business success, rollout authorization or savings claim. If the evidence
does not support a useful proposal, report that rather than guessing.

Review and retain the original sources and AI-returned array. This is a
bounded workgroup review, not a claim that the selected work represents
an entire department or company.

## Review the complete returned population

```sh
python -m shared.ai_implementation_batch review \
  --source original-evidence.json \
  --packet-sha256 ORIGINAL_PACKET_PIN \
  --proposals returned-proposals.json \
  --output batch-review.json --confirm-local
```

Repeat --source in the exact original order for multiple bundles. The batch
is limited to twenty items and 2 MiB of canonical UTF-8 JSON. All returned
items remain in original order with their exact digest and disposition:
structurally reviewed, structural review refused, or exact duplicate of
an earlier item (including duplicates of rejected items). The first exact
occurrence is not a quality ranking. Similar but nonidentical proposals
are not silently merged. A malformed individual item remains visible with
a refused disposition; malformed overall JSON or invalid source packet
is refused as a whole.

The report includes union citation coverage across validated proposals,
uncited run references, coverage gaps and per-proposal structural receipts.
Citation coverage is not semantic accuracy, discovery completeness or
representativeness. Retain the original batch separately: the report does
not reproduce the original proposal bodies.

## Review and choose individual candidates

Retain the exact batch-review pin independently. After a human has reviewed
the report and original proposals, explicitly select a distinct structurally
reviewed item by its zero-based index:

```sh
python -m shared.ai_implementation_batch select \
  --source original-evidence.json \
  --packet-sha256 ORIGINAL_PACKET_PIN \
  --proposals returned-proposals.json \
  --batch-review-sha256 EXACT_REVIEW_PIN \
  --item-index 2 --output selected-proposal.json --confirm-local
```

The selection recomputes the complete review against the original inputs and
refuses stale pins, rejected items and duplicates. The output is the canonical
original v1 proposal. Import it into the existing implementation opportunity
dashboard, provide human-reviewed title/details, and independently review
and confirm creation of an unassessed idea or a shadow skill draft.
No automatic ranking, portfolio writes, model requests, deployment,
spending or execution occurs. The existing single proposal path is unchanged.

The same review is available in the local implementation dashboard: prepare the shared evidence packet, expand **Review multiple AI implementation proposals**, and select the returned JSON array. Review the complete report and download it before modifying inputs. Select an individual structurally reviewed item to transfer its exact canonical proposal to the existing individual review; the human-entered title/details and confirmation are reset. The server independently recomputes the original batch and verifies its pin before selection. Source, budget or batch edits invalidate the batch report and selected batch proposal. There is no automatic creation, ranking or approval. The existing CLI remains available. The original source documents,
returned batch and reports may be sensitive; retain and share according
to existing organizational permissions. Outputs are bounded exclusive local
files.

## Validation

```sh
python -m pytest -q tests/test_ai_implementation_review.py tests/test_ai_implementation_batch.py
```

Tests cover the exact original review contract, mixed valid/invalid/duplicate
population, citation union, all-invalid input, stale pins, explicit item
selection, input bounds and exclusive canonical export. Actual multi-workflow
AI discovery quality and enterprise acceptance require real-data evaluation.
