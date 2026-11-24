# Reviewed-case binding readiness and schema drift (R17)

The generated Agents SDK runtime now supports a read-only, offline contract
audit across every accepted route in an explicitly reviewed case suite.
This closes a gap between suggesting tool mappings and preparing one
customer-authorized case: an operator can see which mappings are incomplete,
incompatible with the inspected metadata, or not exercised by their cases.

## Operator journey

1. Export a trusted, version-pinned runtime project. Inspect the customer
   MCP endpoint with the existing explicit `configure.py --inspect-tools`
   command; retain the resulting minimized catalog in the customer workspace.
2. Prepare a reviewed case suite using the existing shadow-case format and
   create or review bindings. Proposed bindings may remain disabled; this
   audit does not enable them.
3. Audit locally, with no model or provider access:

       python configure.py --audit-bindings --catalog current-tools.json --cases reviewed-cases.json --bindings bindings.json --output binding-audit.json

4. Review the findings by opaque case ID, route alternative and stable
   activity ID. Correct mappings or collect missing cases, then rerun with
   a new output file. Use the separate customer-intent and execution
   approval flow only after human review of the actual provider semantics.

Optionally compare current metadata with an earlier inspected catalog for the
same exact workflow and endpoint:

       python configure.py --audit-bindings --catalog current-tools.json --previous-catalog earlier-tools.json --cases reviewed-cases.json --bindings bindings.json --output binding-drift-audit.json

The current catalog must be recent (within 24 hours, as in mapping proposals).
The prior catalog may be older, but must not be newer than the current one.
A changed schema pin requires review even if bindings were already repinned.
The comparison includes action and precondition contracts, top-level field
addition/removal/type/requiredness counts and missing projections. Absence
from a bounded catalog does not establish removal from a provider.

## Report semantics

- Every selected case and distinct accepted route is included: at most 50
  cases and 80 total alternatives. Invalid, duplicate or over-budget
  populations are refused rather than partially evaluated.
- Exact workflow, spec, case-suite, bindings and catalog digests are recorded.
- The existing runtime `prepare` contract is exercised without execution.
  Disabled or unauthorized bindings, missing source mappings, conditional
  writes, idempotency, and missing human resolution are not inferred away.
- Independently, even disabled proposals are checked for argument resolution,
  top-level type/required-field compatibility and matching schema pins.
  A rejected preparation is an explicit generic finding, without private
  exception text. Unknown types and absent projection entries require review.
- Clarification-only cases are counted separately: they have no accepted route
  to test. Missing normal/exception/missing-facts categories and uncovered
  nonhuman activities or structural transitions require review; neither establishes a population sample. Parallel route coverage uses token replay rather than adjacent node pairs, so interleavings do not invent edges.
- Exit 0 means selected static checks and stated coverage checks are clear.
  Exit 2 means review is required. Invalid inputs refuse without a report.
  Human resolutions, source checks, and separate execution intent remain
  mandatory even when static checks are clear.

The report is limited to 2 MiB, never overwrites files, and contains only
minimized findings, counts, identifiers and hashes; not tool names, raw schema,
argument values, case text, or endpoint URLs. Treat input digests and stable
activity IDs as potentially sensitive identifiers and store them appropriately.

## Boundaries

The audit does not contact any endpoint, run a planner, invoke a business tool,
verify source outcomes or authorize execution. The current catalog is a
previously inspected snapshot, not a fresh provider read; schema constraints,
provider semantics, target-service permissions, and full runtime behavior
remain outside this static projection. The separate existing live execution
checks are unchanged. No mappings, review flags or source records are modified.

The generated configuration test suite includes static contract checks,
historical drift after repinning, disabled mappings, malformed or stale
catalogs, missing case input, category coverage, and offline CLI replay.

Related: [#370 R17](https://github.com/KAVentures/openworkgraph/issues/370).
