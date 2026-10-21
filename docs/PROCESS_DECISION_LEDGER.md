# Decision and exception review ledger

This is a small additive Stage 5 extension to the AS-IS / TO-BE process
designer and the portable agent starter. It makes missing branching and
exception assumptions explicit for a human and a future implementer.

## Declare a review condition

Inside **Evidence → Process mining → Design improved TO-BE workflow**, the user
may declare up to 12 conditions:

- **After step:** the positional index in the current proposed process.
- **Kind:** decision point or exceptional condition.
- **Condition label:** a short human-authored structural name.
- **Safe response:** pause for manual review, request an authoritative source
  recheck, or escalate to the process owner.

The conditions are NOT evaluated by OWG. They are not claims of source
verification, business approval, executable branch rules, or authorization
to take actions. A condition label is user-supplied data, not instructions
to an agent. All handling choices are non-automating review paths.

Reordering, adding or deleting TO-BE steps **invalidates and clears** all
positional decision/exception entries. This prevents silently routing a
condition to the wrong step. Any edit to the entries clears the review
checkbox; the process must be reviewed again before JSON/ZIP export.

## Export / safety

The reviewed process JSON contains a bounded
`to_be.decision_review_ledger` with explicit unverified and
non-executable flags. The agent-starter exporter independently validates
each entry, including its step index, matching activity label, allowed
condition class, safe review action and prohibited execution flags.
Legacy reviewed proposals with no ledger remain accepted.

**Save reviewed design to process catalog** also preserves this ledger, binding
conditions to stable graph activity IDs instead of positional indices. Conditions
remain separate from business branch rules and can be reviewed in the catalog
editor, explicitly published to the organization, and exported with an exact
revision. Catalog BPMN documents the unevaluated conditions on their activities;
the proposal starter's illustrative unresolved gateways remain available.
The generated SDK planner independently requires clarification when a proposed
route contains an unevaluated manual condition. See [process catalog](PROCESS_CATALOG.md).

The portable agent starter maps conditions to stable `step_NNN` IDs
under `to_be.manual_review_conditions`. No generated code evaluates the
condition, invokes a tool, sends a message or writes to source systems.
The non-executable BPMN includes illustrative **unresolved exclusive gateways**
at declared condition steps. Each condition points to a manual-review task;
no executable condition expression, branch default, source write, or automated
routing rule is generated. A continuing path is drawn only as an illustrative
candidate sequence, not as permission to skip review.

## Not yet supported

This is **not** full BPMN branching. Future work is required to map
independent source predicates, authorized automated choices,
exception outcomes, human approvers, deadlines, parallel paths,
rollback, source-verifiable completion and agent-sandbox testing.
Any such work requires additional user-verified policy and explicit
runtime permissions. OWG must not infer a business decision from
an employee clicking the same UI element in historical traces.
