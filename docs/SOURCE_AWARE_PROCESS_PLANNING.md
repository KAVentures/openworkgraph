# Current-source route proposals

Generated projects include an optional read-only `plan_sources.py` command.
It lets the existing no-tools SDK planner use reviewed current-source invariants
when proposing a route. Normal `agent.py` behavior is unchanged: structured
conditions without this source context still require clarification.

Install the generated requirements in an isolated environment, configure the
existing local OWG source-read credential, and explicitly run:

```sh
python plan_sources.py --case case.json --bindings bindings.json --model MODEL \
  --confirm-source-reads --confirm-model-rule-check-sharing > proposal.json
```

Both confirmations are required before source or model calls. The chosen case
and saved process are sent to the selected model, as with `agent.py`. This mode
additionally sends bounded rule IDs and match statuses. It adds no raw source
values, receipt timestamps, selected references, credentials or provider error
messages to the model context. The case itself may contain references or other
customer-authorized data; inspect it before authorizing model transfer. A match
status may reveal information about a declared condition, so sharing those
results needs its own explicit confirmation.

Use the same exact-revision [reviewed invariant bindings](RUNTIME_SOURCE_RULE_BINDINGS.md)
as the executor. Each bound rule must be confirmed, have a supported structured
predicate, and explicitly use `invariant_before_each_action`. Case-to-record
semantics and non-atomic reads must be reviewed. Planning does not enable the
execution bindings. Only selected canonical observed records may be read;
existing source opt-ins and server-side AI/history grants remain in force.

The driver reads one minimized receipt per unique selected reference/type and
keeps it only in memory for that proposal. Rules sharing that selection use the
same snapshot. Different records have separate read times, not an atomic
cross-system snapshot. Inputs are pinned in memory so mutations cannot substitute
another process or case after reading the source. Shared-catalog projects also
need the existing catalog-read credential; the exact current independently
reviewed design is checked before source reads and again after the model.

The model receives only pass/fail/unknown rule checks. An independent post-model
check validates the chosen graph route and reevaluates used source predicates
against the pinned receipts, including freshness. This is repeated after the
final catalog read. A false, unknown, stale, wrong-record or unbound check cannot
support a proposal even if the model claims otherwise. Missing source data
never satisfies an absence predicate. Manual guards and unconfirmed rules still
require review. Provider failures become unknown without forwarding private
errors. A source can change during or after planning; these are as-of proposals.

The resulting `proposal.json` contains a normal plan plus a minimized
`openworkgraph.current-source-planning.v1` report. It contains rule IDs/statuses,
the process reference, a bindings hash and explicit non-authorizing flags. It
contains no raw receipts, source values or native record identifiers. The SDK
trace exporter stays disabled and no business tools are registered or called.

Review the proposal separately, then prepare and approve an exact-artifact
execution intent using the existing `execute.py` flow. Planning creates no
execution intent or permission. The executor independently reads fresh source
state before actions; a saved proposal or its comparison report cannot bypass
those checks. Source matches prove neither business-case association nor policy
compliance, agent causality or business outcomes. General temporal branch
semantics require a workflow-specific implementation.

Tests run the actual SDK Runner with owned in-memory models and a synthetic
source HTTP server. They cover alternative routes, false/unknown/stale results,
consent, privacy, review revocation and the complete proposal-to-authorized-MCP
flow. No live model provider or customer account is contacted. Generated
stdlib-only offline suites skip dependency-requiring SDK/source checks until
requirements are installed; structural checks continue to run.
