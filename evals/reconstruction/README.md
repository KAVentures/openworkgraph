# Workflow reconstruction evaluation

> Secondary evidence-sufficiency diagnostic; primary real-agent study:
> [../README.md](../README.md).


This benchmark tests a narrow product thesis:

> Is OpenWorkGraph's privacy-hardened observed evidence rich enough for a capable
> external AI to reconstruct real human work when several workflows are
> interleaved, without trusting OWG's inferred task labels?

It is separate from the continuity eval and the automation-interpretation eval.

## What it tests

Real work is not a sequence of clean task-sized blocks. People routinely do
A1 -> A2 -> B1 -> C1 -> A3 -> B2 -> A4. The benchmark therefore makes workflow
membership non-contiguous and includes:

- two tasks in Gmail or Salesforce at the same time;
- three interleaved workflows;
- long interruptions followed by resumption;
- shared resources used by different tasks;
- missing resource references;
- cross-app clipboard linkage;
- human and agent activity interleaved;
- a deliberately unobservable approval rule.

Ground truth is event-level. The model never receives workflow IDs, checkpoint
IDs, or the answer key.

## Corpus

fixtures.py is the committed answer-key generator for 54 deterministic synthetic
cases spanning 34 independent scenario families. The original ten families retain structural
variants for regression coverage; twenty-four additional families use distinct topologies.
All synthetic resource, tab, transfer and page-path identifiers are deterministic opaque
tokens: equality relationships remain visible, but fixture roles such as noise/reference/
distractor are never encoded in IDs. Generated JSONL is intentionally not committed, which keeps the repository
small while making the corpus exactly reproducible.

Synthetic cases are engineering tests, not evidence of product value. The corpus deliberately
includes same-tab multiplexing, missing resource references, 4–5 concurrent workflows,
abandoned work, near-duplicate resources, ambiguous interruptions, multiple independent
uncertainty families, and a 50+ event session. A deterministic roughly-20% holdout can be
exported with `prepare --split holdout`; use `--split development` while iterating. Splitting
is by independent family, so structural variants of one family can never appear on opposite
sides of the holdout boundary. Because the answer-key generator is open source, this
is a procedural holdout rather than a secret test set: do not inspect/tune against holdout
predictions during development.

After the synthetic benchmark is stable, run the same protocol on 20-30 manually annotated
real sessions. Those sessions, not synthetic scores, answer the product question.

Validate the built-in corpus:

    python -m evals.reconstruction.fixtures

Export it when a runner needs standalone JSONL:

    python -m evals.reconstruction.fixtures --output /tmp/reconstruction-cases.jsonl

## Stage A: raw evidence sufficiency

This is the most important first test. It removes OWG's derived task/family
grouping from the experiment.

    python -m evals.reconstruction.prepare --output /tmp/reconstruction-prompts.jsonl

For every row, send the supplied system_instruction plus presented_evidence to
the model under test and save the returned JSON as one JSONL row. Never expose
ground_truth.

Score:

    python -m evals.reconstruction.scoring --predictions /tmp/model-predictions.jsonl

Record the exact model ID, client/version, settings and repository SHA with the
results.

### One-command external runner

For a genuinely blind model run, use the external runner. The request contains only
`case_id`, `presented_evidence`, and the reconstruction instruction; it never sends
`ground_truth` or `source_events`.

Direct OpenAI-compatible endpoint:

    export OPENAI_API_KEY=...
    python -m evals.reconstruction.run_external --model <exact-model-id>

For development/holdout discipline, add `--split development` while iterating and run
`--split holdout` only for the final blind check. Split-specific artifacts use distinct
filenames, so they do not overwrite one another.

Vercel AI Gateway (also OpenAI-compatible):

    export AI_GATEWAY_API_KEY=...
    python -m evals.reconstruction.run_external \
      --base-url https://ai-gateway.vercel.sh/v1 \
      --api-key-env AI_GATEWAY_API_KEY \
      --model <provider/model-id>

Use an exact currently supported model ID from the provider/gateway model catalog.
Do not commit credentials. Temperature is omitted by default for compatibility with
reasoning models; pass `--temperature` only when the selected model supports it.

The runner writes ignored local artifacts under `evals/reconstruction/results/`:
predictions JSONL, a scored report, and run metadata including revision, model,
usage metadata returned by the endpoint, and elapsed time. `--resume` continues a
partially completed run without re-paying for completed cases.

## Stage B: real OWG/MCP path

An executable blind replay now exercises the shipped connector policy, Gateway
store, signed OAuth JWT verification/JWKS, public MCP redaction and pagination:

    python -m evals.reconstruction.replay --detail rich --output /tmp/hosted-rich.jsonl
    python -m evals.reconstruction.replay --detail compact --output /tmp/hosted-compact.jsonl

Every case uses a separate disposable OAuth identity. Temporary credentials/database
are deleted afterward; no ambient production OWG endpoint is used. Output contains
only blind presented evidence plus measured replay counts, never source events or truth.
Chronology is shifted into the current retention window while preserving time gaps.
Compact and rich responses intentionally differ; compare them as separate conditions.
This starts at synthetic stored-event input and does not exercise a real OS observer.
Browser runtime tests and the local-text-to-cloud OAuth regression cover capture boundaries.

Interpret the comparison this way:

- Stage A good, Stage B worse: retrieval/navigation/grouping is likely the bottleneck.
- Stage A poor: the observed evidence itself is probably missing signal.
- Stage B better without higher contamination: derived navigation is helping.

Paid model calls do not belong in ordinary CI. The model run is an external
evaluation step, like the existing automation-interpretation eval.

## OWG-native grouping

Score OWG's own shipped exact-record grouping (`record_case_lanes`) on the same
answer key, with no model involved:

    python -m evals.reconstruction.owg_native --split holdout

Lanes publish no event IDs; for scoring only, each published lane is projected
back to **admitted, visible** events carrying its opaque reference. The adapter
uses the shipped record-lane identity and UTC timestamp admission rules, retains
the first valid occurrence of a repeated (record, event ID), and does not score
invalid timestamps, nonliteral/malformed event IDs or source-only rows absent
from `presented_evidence`. The source lane's duplicate admission normalizes
an ID before reserving its (record, ID) slot: for example, source numeric `17`
consumes the slot of a later string `"17"`, but the numeric observation is
not scoreable as a visible literal ID. The evaluator mirrors that ordering
without inventing a string-ID assignment; a falsey numeric `0` does not
consume the literal `"0"` slot because production treats it as an empty ID. A reused event ID admitted under two distinct
records is withheld from both rather than assigned to an arbitrary lane.
A matching ID alone is insufficient: the visible event must also carry a
single consistent normalized (opaque reference, provider, resource kind, host)
identity **and the same admitted UTC observation instant** as the source row.
Different timezone offsets representing the same instant are equivalent; a
microsecond difference is not. Visible record identity components must be
**literal strings when present**, before case-insensitive normalization:
boolean providers/kinds and numeric or null hosts cannot impersonate source
strings such as `"true"` or `"123"`, or a missing host. Valid literal
case variants and legacy absent hosts remain accepted. This visible-only
admission check does not rewrite the shipped source engine's identity rules
or independently authenticate a provider. Missing, malformed, contradictory or
cross-provider visible identity or timestamp is withheld, not silently
replaced with source-only provenance. Repeated visible IDs with the same
identity are allowed only when one visible timestamp matches the first
source-admitted observation; a later duplicate cannot stand in for it.
The rich evidence format intentionally omits organization identity, so the
adapter cannot independently verify a tenant from the presented row; the
production lane's source-side organization/identity checks still apply.
Source rows without a usable literal event ID may still contribute to the
shipped lane's aggregate count but cannot be assigned to a visible event ID
in this evaluation. This is a measurement-integrity correction, **not** a
change to production record grouping, synthetic truth, family split, scoring
formula, thresholds or a demonstrated improvement in held-out F1.

A lane means "same record", never "same task", so a record shared by several
true workflows is reported as contamination rather than hidden.
`tests/test_reconstruction_owg_native.py` holds regression floors on all and
holdout cases and adds adversarial projection checks for invalid timestamps,
duplicate IDs, cross-record collisions, source-only rows, offset ordering,
conflicting source identity, source-versus-visible provenance mismatches,
contradictory visible IDs, mismatched/malformed UTC observation instants,
first-admitted duplicate timestamps, and normalized positive controls.

### Linked-item evaluator visibility and revocation boundary

The `linked_items` and diagnostic `linked_items_accepting_candidates`
conditions now admit source observations **before** invoking the existing
production linker only when their literal event ID is present in the supplied
`presented_evidence`, with the same UTC observation instant, app, event type,
source, session and link-authorizing reference/tab/clipboard/action metadata.
Offset-equivalent instants are accepted; missing or contradictory evidence,
reused IDs with conflicting source or visible claims, and source-only rows are
withheld. Exact duplicate source JSON claims remain idempotent; bounded
canonical JSON comparisons distinguish booleans from integers and refuse
malformed or oversized duplicate claims. The linked-item evaluator also mirrors
the shipped linker's **first valid-timestamp, normalized event-ID admission**:
an earlier numeric `17`, boolean `True` or float `1.0` reserves the same
source duplicate slot as its later literal string ID. That later string is
withheld rather than scored from a source row the production linker suppressed.
The nonliteral row never becomes visible membership. Reversed source order
preserves the literal first-admitted event; malformed timestamps and falsey
nonliteral IDs do not consume an ID slot. Conflicting **timestamp-valid** literal duplicate claims
remain conservatively withheld. The production linker ignores malformed
timestamps before first-ID admission; the evaluator now likewise ignores
invalid-timestamp source duplicates when selecting the first admitted literal
row and checking duplicate conflicts. Thus an invalid earlier or later replay
cannot veto or substitute for a valid observed transfer, while a conflicting
valid replay still fails closed. Synthetic regressions check both source orders
and the candidate-accepting diagnostic. These are evaluator-only parity and
provenance checks, not a change to production deduplication or a held-out
score claim.
**Evaluator duplicate-claim preflight (2026-11-01):** The source replay
canonicalizer now validates JSON shape *before* calling `json.dumps`, rather
than allocating an arbitrarily large serialized string and checking its size
afterward. It caps traversed nodes at 10,000 and the serialized result at
65,536 characters, with an early raw-string budget. Non-string object keys,
nonfinite floats, unsupported Python objects, oversized integers, cycles and
reused in-memory container identities are withheld. Distinct but structurally
equal JSON containers, key-order variations and ordinary literal JSON replays
remain admissible. A rejected duplicate withholds that event ID from
evaluator-only linking, including the candidate-accepting diagnostic; it does
not authorize a time-only join. These are synthetic provenance and resource
bounds, not production-linker validation, a held-out accuracy result or
customer-scale performance proof. The separate unmerged #500 production-linker
draft must not be treated as current `main` behavior.

Rich-evidence top-level tab/transfer fields are convenience copies of
metadata, not independent source claims. Both locations, when present,
now require **literal, pattern-valid matching strings**: Python equality
(`True == 1`) and the current linker's coercion of booleans/numbers into
opaque-looking token strings cannot establish a transfer or tab link.
Malformed metadata-only claims are withheld too; valid legacy metadata-only
strings remain accepted. An unpresented source-only copy/paste row must not
bridge two visible records or create a candidate link.

Explicit record-identity components (`resource_ref`, `provider`, `resource_kind`,
`host`) must also be **literal strings** when present, and an explicitly
present `resource_reference` must be an object. Otherwise the current linker
could stringify a boolean provider, numeric host or malformed reference into
plausible same-record evidence even when the visible and source copies match.
Non-null app labels must likewise be literal strings: a boolean, number or
object cannot authorize a same-app continuity candidate in the diagnostic
mode. Missing references and null/absent app labels retain their no-signal
semantics; valid literal record identity and same-app ambiguity still work.
These are evaluator admission checks, not changes to production grouping or
claims that the identity is provider-verified.

For **valid literal** record identities, linked-item visible/source admission
now compares only the production linker's normalized opaque reference,
provider, kind and host. Case-only differences in those strings and extra
reference annotations that the linker does not use are not reasons to discard
an otherwise identical visible observation. Distinct normalized providers,
kinds, hosts or refs still fail closed; invalid/nonliteral identity claims
remain raw or rejected, never silently converted into valid authority. Rich evidence omits organization/actor/device, so this evaluator
cannot independently attest those scope boundaries. It retains the original
source rows and does not grant permission or claim that production scope
isolation has passed acceptance; separate production-linker hardening is under
review in draft #500. This corrects evaluator-only false-negative admission,
not verified provider identity, cross-record semantic linking, or the
preregistered holdout score.

An explicit `source_events: []` (or null) is authoritative: if source
evidence has been revoked/deleted, neither the record-lane nor linked-item
evaluation may fall back to a stale `presented_evidence` snapshot and
resurrect membership. All still-presented IDs remain unassigned in that
scenario, including diagnostic candidate-acceptance mode. Only an *absent*
`source_events` key retains backward-compatible presented-only evaluation.
Synthetic tests exercise both cases; they do not establish end-to-end
revocation propagation, live source deletion or held-out reconstruction quality.

Visible exact-record lane evidence now has a **single-instant event-ID rule**.
An event ID can be admitted to a source-record lane only if every presented
occurrence has the same normalized UTC observation instant and the same public
resource identity. Equivalent timezone offsets (including fractional seconds)
are valid replays. A second distinct instant, malformed timestamp or changed
identity permanently withholds that ID, even if one visible occurrence matches
the first source-admitted row. Source-only duplicate rows remain governed by
the shipped first-valid admission behavior: a later suppressed source duplicate
does not by itself make a uniquely presented event ambiguous. This prevents a
reused visible ID from lending one event's evidence to a different instant or
record, without changing production grouping or inventing a time-only join.
Synthetic regression tests exercise both orders, malformed late replays and
offset-equivalent duplicates; they are not a held-out score or native CI proof.

Duplicate replay admission is now bounded by **unique literal event IDs** rather
than the number of repeated visible/source observations. For each ID, the
evaluator retains one first-valid source row, one first visible projection and
an irreversible contradiction state. It canonicalizes complete source JSON
only if a second timestamp-valid literal row with the same ID appears; a
malformed/oversized or contradictory replay still withholds the ID. An earlier
valid-timestamp nonliteral ID continues to reserve the production duplicate
slot, and malformed-timestamp rows do not. This reduces evaluator-only
duplicate bookkeeping, **not** the size of the already-loaded synthetic corpus,
the production linker's memory use or any source retention. Large synthetic
replay regressions check idempotence, late contradictions and admission order
without asserting hardware-dependent memory/timing thresholds. These tests do
not demonstrate held-out F1, contamination, or real-world scale.

The linked-item evaluator's **internal visible projection is JSON-native at
every nesting level**: a list of link-authorizing fields, with normalized
record identity represented as a nested list. This matters because the strict,
bounded duplicate-source JSON preflight intentionally rejects Python tuples.
Using tuples for the evaluator's own projection would reject every otherwise
valid linked observation and silently turn accepted memberships into empty
predictions. A direct regression checks nonempty projections, valid observed
clipboard links and same-record membership in both candidate modes, plus
fail-closed contradictory source duplicates. This fixes evaluator-only
admission, not the production linker, and does not establish any held-out
threshold or native CI result.

The filter retains the original authorized source rows for the linker's own
organization/actor/device/session checks. These identities are deliberately
absent from the rich evidence and cannot be independently verified from its
projection. This is an **evaluation admission boundary**, not a new capture,
privacy, source-authentication or production-linking engine. It never upgrades
ambiguous candidates into verified membership; the accepting-candidates mode
remains an explicitly unsafe diagnostic upper bound.

Synthetic regressions cover hidden transfer bridges, changed visible
provenance, exact UTC offsets, duplicate IDs, malformed dual-location and
metadata-only transfer tokens, and legitimate literal visible transfer linking.
Both OWG-native scoring projections also enumerate only **unique literal,
pattern-valid presented event IDs**: booleans, numbers, missing IDs, invalid
strings and repeated visible IDs cannot become fabricated unassigned scoring
items. Such invalid rows never gain workflow membership. This affects only
the evaluator's input hygiene, not the corpus truth or production linker. They do not replace preregistered held-out scoring, real consenting
pilots or native CI. No assignment F1 ≥ 0.90 or contamination ≤ 0.05
achievement is asserted.

### Multi-signal linked items

`server/work_item_links.py` (roadmap #326 A1) extends exact-record lanes. Observations
are merged only by evidence that names the same thing:

- `same_record`: the same valid resource reference;
- `clipboard_transfer`: the same observed transfer id, which may join two records;
- `same_tab_single_record`: an unreferenced observation in a tab that showed exactly one record.

Weaker signals are returned only as `ambiguous` candidate links and are never merged:
`same_tab_multiplexed`, `same_tab_unreferenced` and `surface_continuity`. Time alone never
links anything, and labels, titles, URLs and clipboard contents are not read. The result is
returned as `linked_work_items` in workflow-evidence discovery.

    python -m evals.reconstruction.owg_native --split holdout --grouping linked_items
    python -m evals.reconstruction.owg_native --split holdout --grouping linked_items_accepting_candidates

Identifiable-membership family macro on the committed corpus:

| Grouping | All F1 | All contamination | Holdout F1 | Holdout contamination |
|---|---:|---:|---:|---:|
| Record lanes | 0.541 | 0.000 | 0.476 | 0.000 |
| Linked items | 0.574 | 0.000 | 0.524 | 0.000 |
| Linked items + accepting every candidate (diagnostic only) | 0.708 | 0.050 | 0.649 | 0.125 |

Accepting the ambiguous candidates raises F1 but breaks the contamination gate on the
holdout, which is why OWG leaves them to the reader. Interruption rejection stays at 1.0.
The remaining gap to 0.90 comes from workflows whose records are related only by meaning
(for example a renewal email, an account and a price sheet). Deterministic structural
signals cannot see that, so closing it needs a model-graded run (A2).
`tests/test_work_item_links.py` holds these floors.

## Ablations

After a baseline model run, remove one evidence signal at a time:

    python -m evals.reconstruction.ablations \
      --mode resource_references \
      --output /tmp/no-resource-refs.jsonl

Available modes are resource_references, tab_context, semantic_actions,
safe_labels and clipboard_linkage.

A large quality drop after removing one signal tells us what capture work is
worth prioritizing.

## Prediction contract

Each prediction is one JSON object with:

- case_id
- workflows
- each workflow's event_ids and ordered_event_ids
- automation_relevant_event_ids
- ui_mechanic_event_ids
- unassigned_event_ids
- insufficient_evidence
- optional notes

Workflow names are not scored. Predicted workflows are matched to hidden ground
truth by maximum event overlap.

## Preregistered engineering thresholds

| Metric | Threshold |
| --- | ---: |
| Workflow assignment F1 | >= 0.90 |
| Cross-workflow contamination | <= 0.05 |
| Exact workflow-count accuracy | >= 0.90 |
| Meaningful-checkpoint recall | >= 0.90 |
| Unrelated-interruption rejection | >= 0.95 |
| Hidden-rule uncertainty recall | >= 0.90 |
| Unnecessary uncertainty rate | <= 0.10 |

The scorer also reports ordering accuracy, automation-relevance precision/recall
and UI-mechanic precision/recall. It reports both historical case-level metrics and a
family-macro view. The release gate uses the family-macro metrics so the three variants
in each of families 01-10 do not receive triple the weight of independent single-case
families. Hidden-rule uncertainty is likewise macro-averaged across uncertainty families.

These are engineering release thresholds, not claims of statistical
significance. A publishable study should add a larger preregistered real-work
sample, blinded adjudication, paired resampling/confidence intervals and
multiple frontier model families.

## Interpretation rule

Historical human behavior is evidence, not policy or permission. A model should
use observed work to identify useful checkpoints and information dependencies,
while preferring live authorized connectors/APIs over literal UI replay. A
missing historical payload does not prove an automation is impossible, and an
observed approval does not reveal the business rule that caused it unless that
rule is actually present in evidence.

## Version 2 fidelity and observability

Fixtures now call `shared.evidence.rich_evidence_row` directly, use production-valid
resource references with hosts, use conservative action confidence and follow canonical
timestamp order. Every one of the 54 cases is replayed through the hosted MCP in tests.
These changes invalidate direct comparison with old fixture scores; record the commit
and `benchmark_contract_version` when publishing results.

Generic cross-resource labels without an observed transfer are conservatively flagged
as ambiguous membership. Hidden author intent is retained as a diagnostic partition,
but is not a certainty gate. Assignment thresholds use only the nonflagged membership
families; uncertainty thresholds still cover all families. This is a synthetic
observability screen, not independent human adjudication or proof that every unflagged
case is identifiable. `uncertainty` separates membership, business rule and persisted
outcome; a UI click alone cannot prove a persisted outcome. Legacy boolean outputs
remain supported, with less diagnostic resolution.

Actual cross-workflow mixing is measured separately from pure fragmentation. Fragmentation
still lowers one-to-one assignment F1 and workflow-count accuracy. Unknown event IDs lower
precision and are reported explicitly. The matching algorithm uses bounded-family dynamic
programming rather than factorial permutations of predicted fragments.

Paid/independent model trials and verified real-work episodes remain necessary to
establish usefulness. Passing transport tests, oracle scores or synthetic thresholds
cannot substitute for those measurements.
