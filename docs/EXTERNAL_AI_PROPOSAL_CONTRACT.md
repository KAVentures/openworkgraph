# External AI workflow proposal return contract (P0 #1663)

The existing OWG workflow evidence export already provides observed work
examples and drafting guidance to an external AI. This local command adds
a structured return path for the AI's interpretation without introducing
a model, a new source store or another agent runtime.

## Prepare a proposal request

Export the existing workflow evidence with canonical excerpts included.
For a ZIP export, extract WORKFLOW_EVIDENCE.json locally. Review its
representation and authorization before sharing it with your existing AI.

From the OWG repository:

```sh
python -m shared.external_ai_proposal prepare \
  --evidence WORKFLOW_EVIDENCE.json --output proposal-request.json \
  --confirm-local-review
```

The request contains the exact canonical JSON evidence digest, the required
proposal shape, instructions and coverage metadata, not copies of captured
event text. Keep the independently printed evidence digest. Give the
authorized external AI the request and original evidence export. Ask it to
return a JSON object following proposal_shape with each section populated
by one to twenty entries. Each entry has kind, text and citations. Each
citation is an exact execution_id and event_ref pair from that excerpt.

The goal, actors, trigger, inputs, outputs, variants, exceptions, policy,
unknowns, current tools, redesign and human approval sections are required.
Entries distinguish observed, inferred, proposed and unknown. Observations
and interpretations require evidence references. Policy and human approval
entries must be proposed or unknown; an observed action is not permission.
Unknowns should describe missing business facts or evidence, not invent them.

## Review the external AI's returned proposal

Save the AI's returned JSON locally, then run:

```sh
python -m shared.external_ai_proposal review \
  --evidence WORKFLOW_EVIDENCE.json --evidence-sha256 EXACT_EVIDENCE_PIN \
  --proposal proposal.json --output proposal-review.json \
  --confirm-local-review
```

The validator rejects changed evidence, missing sections, unsupported fields,
fabricated or cross-execution references, ambiguous event identifiers,
inconsistent excerpt coverage and observed/inferred approval assertions.
When the export reaches a source limit, contains a truncated excerpt or has
an empty excerpt, the unknowns section must include an unknown entry.
The report records entry kinds, citation counts, excerpt coverage, uncited
execution counts and exact proposal/evidence/review digests. It does not
copy proposal text or captured event text. Both commands refuse overwriting
an existing output; outputs may still contain sensitive opaque identifiers
and should be handled locally.

A passing review means only that the proposal structure and citation
membership match the supplied artifact. It does not prove that the AI
interpreted the references correctly, the workflow is complete, the evidence
is authentic or still authorized, a policy is valid, or a business outcome
occurred. Human review of the original evidence and material ambiguities
is still required. Do not execute the draft or silently import it as approved
knowledge. This offline first slice does not provide dashboard approval,
live source checking, an MCP write surface, or verified target-client
deployment. Those remain separate P0 delivery work.

## Validation

```sh
python -m pytest -q tests/test_external_ai_proposal.py
```

The tests cover exact execution/event membership, cross-execution and
fabricated citations, required sections and authority boundaries, coverage
and stale pins, no captured/claim text in the report, and exclusive CLI
output. Fixtures match the existing workflow evidence export shape; actual
authenticated export and target-client acceptance still need integration
validation.
