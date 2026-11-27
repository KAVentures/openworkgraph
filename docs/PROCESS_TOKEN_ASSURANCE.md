# Structural token-flow assurance (R13)

In the local **Process catalog**, open any saved revision and expand
**Structural execution assurance**. Click **Analyze saved token flow**.
This explores the existing structural token engine, not live work, and uses
the exact saved revision and SHA-256 digest. Unsaved edits are excluded;
editing or changing the selected design clears results and disables download.

## What the report establishes

- Whether the sole completion marking is reachable.
- Whether every reachable marking has a structural path to completion, if
  the finite marking graph was fully explored.
- Known nonterminal dead-end markings and examples of firing prefixes that
  reach them. A dead end is not a business-system incident.
- Cyclic strongly connected components and concrete example prefix/cycle
  schedules. A cycle shows the structural possibility of repeated firings;
  it is not proof that an actual case will loop indefinitely.
- Each declared activity, terminal and transition with a shortest known
  completed structural firing witness, including gateway firings. Distinct
  IDs remain distinct even if their display labels match.

The graph uses existing OWG edge-token semantics: exclusive branches select
one successor; paired parallel splits start every branch; inclusive splits
start any nonempty subset and their paired join waits for the started branches.
The report does **not** evaluate branch predicates, prove decision coverage for
real business facts, assert absence of every BPMN error, authorize execution,
or verify policy or outcomes. It covers only OWG's supported structured
models, not general BPMN or unbounded multiset Petri nets.

## Bounds and reproducibility

The search visits at most 10,000 markings and 100,000 firings. A complete
exploration gives exact reachable marking/arc and SCC counts for these
structural semantics. If the bound interrupts exploration, negative
reachability and universal completion results remain unknown. Positive
completed witnesses and observed dead-end prefixes still provide evidence.
Partial SCC counts describe the explored subgraph and may change as additional
edges join components.

Witness paths display at most 80 node IDs; truncation is explicit and the
full firing count and union of emitted transition indices are provided.
At most 12 example cycles and dead ends are included. Transitions use
zero-based indices in the exact saved revision's transition list. Witnesses
are descriptions of structural possibilities, not executable agent plans.

## REST

`GET /v1/process-catalog/token-assurance?workflow_id=...&revision=...&content_sha256=...`

Requires an authenticated dashboard session. A mismatched digest is a 409;
a deleted or missing revision is a 404. A general AI bearer does not grant
access to this route. The response is no-store, computed on explicit request,
and not persisted. Download the JSON separately in the dashboard.

The underlying model, revision, policy approvals, agent mappings and source
data are never modified. No model service, external tool or provider is called.

## Acceptance

Tests use hand-enumerated exclusive, parallel and inclusive examples and
replay each returned completed witness with the shared structural engine.
Loop tests check the actual reachability of cycle witnesses; budget tests
ensure unknown is preserved, and HTTP tests check the exact revision/digest
and dashboard boundary. This is synthetic structural validation, not
customer acceptance or a substitute for independent business-rule tests.
