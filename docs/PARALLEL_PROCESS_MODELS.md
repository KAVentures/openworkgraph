# Explicit parallel process design and structural analysis

The catalog supports declared `parallel_split` and `parallel_join` activities.
A split activates every branch; its paired join waits for every branch before
continuing. These are design declarations. Interleaved timestamps or desktop
observations never automatically become parallel policy or prove simultaneous
work, employee handoffs, active effort or successful outcomes.

## Supported design

A split has 2–8 unconditional branches ending at one distinct paired join.
Each branch is a disjoint linear sequence of activities or single-exit routing
nodes. One direct split-to-join branch can be empty. The join consumes exactly
those branch arrivals and has one continuation. A split accepts at most one
incoming transition, and an entry split has none. A join cannot be the process
entry or terminal. Use an explicit exclusive merge before a split when routes
converge outside the region.

Multiple regions can appear in sequence. An explicitly declared exclusive loop
outside a region can repeat it after the previous join completes. Nested forks,
conditional/decision branches inside regions, shared intermediate activities,
escaping branches, unmatched joins and mixed join/split gateways are rejected.
Existing serial/exclusive designs and their serialization remain unchanged.

Local and shared editors offer both kinds and render a plus marker with the
parallel kind. Saved revisions, declared responsibilities, manual review
conditions, organization roles, independent revision review and history use the
existing catalog. No second model store or observation collector is introduced.

## BPMN interchange

BPMN imports recognize supported `parallelGateway` split/join topology.
Declared `gatewayDirection` must agree with it; parallel default routes,
conditional expressions and predicates are rejected. Plain branch annotations
remain untrusted preview notes with a manual-review guard rather than becoming
conditional routing. Original source IDs and notes are not retained in the
model. Previewing a file neither saves it nor approves policy.

BPMN exports preserve both gateways, explicit direction, every branch and
portable diagram geometry, with `isExecutable="false"`. JSON and PDD retain
parallel activity kinds. External bpmn-js round trips check the actual gateway
nodes and directed edges. Other unsupported BPMN semantics remain rejected.

## Structural conformance

Parallel analysis uses token markings on graph edges. The fork emits separate
branch tokens and the join consumes all arrivals, preserving branch order while
accepting valid interleavings. A recorded path missing one branch cannot be
accepted as an exclusive choice. Repeated labels still represent distinct model
activities. Start/end, decision and routing nodes remain structural nodes,
not required source events. Declared rule conditions are not evaluated.

As with serial comparison, completed searches return one minimum unit-cost
activity insertion/deletion alignment. Several minimum alignments can exist;
this is no business explanation. At most 80 operations are displayed. A valid
prefix may reflect incomplete observation rather than an exception. Timestamp
ties retain supplied order without verifying that order.

Search is bounded to 100,000 discovered states per full path and 200,000 states
across all distinct full paths in one analysis/comparison. Work allocation uses
stable full-path ordering; frequency rank is not identity. An unfinished search
returns `comparison_complete: false`, a budget reason and null match/edit/prefix
verdicts. It establishes neither a match nor a deviation. Summary reports count
unknown cases separately, retain all-case attempts and expose incomplete coverage.
Snapshot/cohort drift with unknown cases does not report a match-share change.
Browser summaries and variant inspection show the unknown result explicitly.

These are structural comparisons of supplied chronology with a declared model.
They do not verify parallel execution, business conditions, source completeness,
legal compliance, policy authority or business outcomes.

## Agent handoff boundary

BPMN/JSON/PDD handoff preserves the design. The current generated SDK planner and
customer executor use a linear route contract and cannot execute a fork/join
process correctly. Their project export is disabled in local/shared views and
rejected by both APIs, even after independent organization design review. Core
project generation and generated runtime validation also reject parallel models,
including a manually rehashed specification. No branch is silently omitted or
serialized into apparent parallel execution. A fork/join-capable runtime and its
separate customer tool permissions remain future work.

## Verification

Tests enumerate every interleaving of two ordered branches, compare minimum
edit costs against an independent finite-language oracle, preserve separate
repeated-label tasks and empty branches, reject missing/early/escaped joins,
exercise external loops and serial regions, and check per-path/batch budget
exhaustion and unknown totals. API and shared-store checks preserve review,
tenant isolation, descriptive exports and runtime refusal. Browser acceptance
covers import/review/save, keyboard/mobile use, actual accepted interleaving
versus a missing branch, exact-revision analysis and unchanged canonical data.
