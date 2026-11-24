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


## Compare a baseline and candidate binding configuration

Use the same explicitly reviewed suite and saved inspected catalog to isolate
changes to customer bindings:

    python configure.py --audit-bindings --catalog tool-catalog.json --cases reviewed-cases.json --baseline-bindings previous-bindings.json --bindings candidate-bindings.json --output paired-binding-review.json

Both configurations are audited independently on every reviewed alternative.
The comparison pins the exact specification, suite, catalog and two binding
digests. It partitions alternatives into improved, regressed, unchanged-clear
and unchanged-with-findings, and lists findings added and removed even when
the binary static-check result is unchanged. Changes in separate human
resolution requirements and contract counts are reported. The comparison
identifies added, removed, modified and reordered retained activity bindings,
as well as changes outside the binding list, without copying their values.

Any binding configuration change requires review (exit 2), even if the
candidate passes every static check. A different result predicate or argument
mapping may change behavior without introducing a projected type error. The
candidate's static review status is reported separately. Identical inputs
with a clear audit may exit 0. The command cannot be combined with
`--previous-catalog`, which is a separate catalog-drift comparison. No
network or business-tool calls, execution authorization, automatic migration
or runtime compatibility claim is made.

The exact extracted route-delta function (2,605 characters, FNV-1a
1907522087) passed 1,000 randomized report pairs covering 39,973 route
comparisons and five mismatched-basis refusal checks in a local isolated
test. This validates the delta algorithm, not end-to-end generated runtime
integration. Additional generated-runtime and CLI regression tests are
included; full exact-head CI is required.


## Turn one reviewed route into actual executor inputs

No further planner model call is required when an authorized customer has
already reviewed the exact route and case in the suite:

    python configure.py --prepare-reviewed-case --cases reviewed-cases.json --bindings bindings.json --case-id case_1 --alternative 1 --confirm-retain-case-inputs --output prepared-case

The case ID and one-based alternative must be explicit. Clarification-only
cases are refused, rather than silently choosing an executable route. The
same runtime preparation contract used by execute.py validates the selected
case, model and mappings. The new directory contains case.json, plan.json,
bindings.json, customer-intent.json and preparation-review.json. The first
four can be passed directly to the existing executor. The minimized review
pins the selected case, suite, route, bindings, spec and disabled intent and
reports offline implementation requirements and human/source dependencies.

The explicit retention confirmation is necessary because the directory
contains actual selected case values and binding configuration, not merely
a minimized report. Store it in a trusted local folder, protect it under
customer retention policy and never commit it or share it automatically.
Each file is bounded to the executor input limit, created exclusively with
private POSIX permissions; Windows inherits ACLs. No existing files are
overwritten. Failed writes attempt to remove only the newly created files.

Review using the existing offline executor:

    python execute.py --plan prepared-case/plan.json --case prepared-case/case.json --bindings prepared-case/bindings.json

All implementation requirements must be resolved. The emitted intent has
authorization and source-read confirmation false and no expiry; it cannot
execute as generated. An authorized human must separately review the exact
inputs, resolve requirements and supply the existing exact customer intent
before using the existing execution mode. No model, source provider, tool
or execution call occurs during preparation. A reviewed expectation is
not evidence of current provider state or permission to act.

Local validation of the exact extracted directory writer (1,596 characters, FNV-1a 352013743): 100 real filesystem round trips with permission and JSON checks, 100 overwrite refusals, five injected file-open failures and three invalid-input refusals passed. Full route preparation and generated executor integration tests are committed but have not been run locally.


## Reuse a reviewed route on a new business case

    python configure.py --prepare-reviewed-case --cases reviewed-cases.json --bindings bindings.json --case-id case_1 --alternative 1 --new-case authorized-new-case.json --confirm-retain-case-inputs --output new-case-prepared

This is a deterministic route reuse operation, not a new planner prediction.
The selected suite case and alternative supply the route only. The newly
selected input supplies every case argument, result predicate input, source
record reference and other case-bound value through the normal executor
preparation contract. The old case values are not copied into the new
package. The review retains the original case digest for lineage and the
new case digest for exact intent binding. The disabled intent is freshly
generated. A reviewed route for the old case does not establish its
applicability or authorization for the new case, even if the inputs happen
to have the same bytes. An operator must assess the new case and resolve
any missing facts, current conditions and binding requirements before
authorizing execution. Clarification-only reference cases cannot provide
an executable route.

The exact extracted route-materialization function (3,326 characters,
FNV-1a 3969273020) passed 2,000 locally exercised original/new-input selection
and intent handoff cases and seven refusal cases using stubbed suite
validation and preparation. Added generated-runtime tests exercise actual
executor preparation and synthetic tools/source receipts on a different
case, including refusal of the disabled intent and reuse of its later
synthetic authorization with the original case. These end-to-end tests
remain pending full CI; no real provider was contacted.


## Shortlist reusable reviewed routes for a selected case

    python configure.py --shortlist-reviewed-routes --cases reviewed-cases.json --bindings bindings.json --case authorized-new-case.json --output route-shortlist.json

This offline command groups identical reviewed activity sequences and checks
each distinct alternative against the selected input using the existing
executor preparation contract. Every alternative retains its suite case ID
and one-based route index, usable by the separate preparation command.
The report lists implementation blockers and whether human resolution or
source checks are needed. It does not copy selected case values or tool
arguments, although activity IDs and case digests may themselves be sensitive.

If the selected input has an exact canonical digest match with reviewed
inputs, only the intersection of their accepted routes is supported by
all exact reviews. Conflicting reviews and clarification expectations are
surfaced explicitly. A unique exact reviewed route with clear static
requirements is not execution permission, proof of applicability or a
current policy check. For a new input with no exact reference match, even
a single statically clear candidate remains a suggestion requiring review.
The list is deterministically ordered and no route is automatically
selected; the operator separately chooses the appropriate case ID and
alternative using the existing preparation command. No new plan or intent
file, tool call, source read or model request is produced by shortlisting.

The exact extracted inventory function (919 characters, FNV-1a 4041194232)
passed 1,000 local randomized cases covering 21,423 route references against
an independent intersection and provenance oracle using a local canonical
JSON digest helper. Runtime integration and CLI regression cases have been
added but remain pending full CI.


## Triage a selected queue of new cases

    python configure.py --shortlist-case-batch --cases reviewed-cases.json --bindings bindings.json --case-batch selected-cases.json --output batch-review.json

The selected input document has format
`openworkgraph.selected-route-cases.v1`, explicit
`case_inputs_authorized: true`, and a cases array of objects with
`id` (case_ followed by 1–4 digits) and `input` (the selected JSON
case object). Select 1–20 cases and no more than 400 distinct case-route
checks. The whole input and work budget are checked before triage. Every
case is reviewed by the same single-case shortlist logic. The report
preserves the selected IDs and case digests, per-case route candidates,
their reference case and alternative index, static requirements and
human/source dependencies. Identical selected case inputs are flagged
but never silently dropped or treated as independently identified
business records. The report contains no case values or tool arguments.

Use the resulting reference case and alternative with the separate
reviewed-case preparation command and original selected input after
reviewing the new case. Batch triage does not select or execute a route,
authorize actions, create intents or contact a model or provider.

Targeted local validation: the exact batch function (4,079 characters,
FNV-1a 3937536043) passed 1,000 randomized batches covering 105,768
simulated case-route checks and nine invalid-input/work-budget refusals.
The suite validator and single-case shortlist were simulated in that
test; generated-runtime and CLI integration regressions are committed
but require CI.
