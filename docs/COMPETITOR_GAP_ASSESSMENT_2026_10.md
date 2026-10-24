# OWG competitor gap assessment — 24 October 2026

## Decision

OWG has broad first implementations across task mining, process mining, process
knowledge and agent handoff. It does **not** yet demonstrate complete functional
parity with the combined capabilities of Mimica, UiPath, Celonis, Soroco,
Microsoft, SAP Signavio, ABBYY, IBM, Apromore and Disco. The largest remaining
work is connecting those implementations into a reliable company product and
proving their accuracy and business outcomes.

This assessment supersedes the old *gap descriptions*, not shipped functionality,
in [#264](https://github.com/KAVentures/openworkgraph/issues/264). Keep
[#326](https://github.com/KAVentures/openworkgraph/issues/326) as the delivery
history. Checked first-slice tasks are not evidence of complete parity.

## Method and limits

Repository baseline: `4bb6ee349d1c` (main, including PRs #366–#369). Evidence below
is from source, tests, documentation and public primary vendor pages retrieved
on 2026-10-24. Repository paths are relative to this checkout. Vendor sources
are numbered at the end. Product pages establish **advertised functionality**;
user guides establish **documented functionality**. Neither establishes runtime
quality, contractual availability, editions, deployment fit or performance.
No paid competitor environment or private customer dataset was accessed.

The vendor capabilities form a union, not a claim that every competitor offers
every feature. Do not turn vendor marketing percentages into OWG acceptance
thresholds. We inspected REA's role: public documentation was sufficient here;
no proprietary code, protected application, binary or asset needed inspection.

Notable corrections to the earlier comparison:

- UiPath's current Task Mining guide explicitly scopes it to **known tasks** and
  excludes automatic discovery of unknown tasks and ranked recommendations [S2].
  Mimica's broader discovery claims [S1] are a separate benchmark.
- Cartographer now describes a governed **Map of Work** in Open Knowledge Format,
  guided discovery, fact provenance, exceptions, named approvals and generated
  PDD/SDD [S3]. A diagram plus exported document is only part of that experience.
- Mimica Maker advertises agent code tested against actual work examples [S1].
  OWG's runnable synthetic SDK/MCP proof is valuable but does not prove that claim.
- Microsoft documents scheduled and incremental refresh, shared analytics, and
  transformations [S7–S9]; those go beyond uploading CSVs or sharing a graph.
- Celonis advertises a connected context model and querying billions of records
  [S10]. OWG's local 10M-event acceptance is a different, narrower scale claim.

## What already exists

Do not rebuild these: local desktop/browser evidence and consent; discovery
studies; versioned process catalog and independent organization design review;
CSV/XES/OCEL 2.0 import/export; columnar saved logs and incremental append;
variants and evidence drill-down; paired/nested parallel structural models;
conformance/alignment/token replay; KPIs, typed analyses, compliance rules,
root-cause **associations**, predictions/backtesting, map abstraction, dotted
charts, cohort comparison and concept drift; simulation; opportunity scenarios
and a local idea pipeline; masked screenshot PDDs; selected communications-export
mining; generated starters and executable customer-controlled SDK projects;
shadow evaluations, pinned source checks and minimized execution monitoring.

Relevant inventories: [process roadmap](PROCESS_PRODUCT_ROADMAP.md),
[event-log workspace](EVENT_LOG_WORKSPACE.md), [catalog](PROCESS_CATALOG.md),
[shared design collaboration](PROCESS_COLLABORATION.md),
[monitoring](AUTOMATION_MONITORING.md), [pipeline](AUTOMATION_PIPELINE.md).

## Capability gap matrix

“Partial” means implementation exists with a material limitation. “Missing”
means no complete product path was found in the inspected baseline, not proof
that no related helper exists. “Unproven” means acceptance evidence is missing.

| ID / priority | Competitor reference | OWG evidence and remaining work | Completion evidence |
| --- | --- | --- | --- |
| R1 / P0 | Automatic discovery, variants, cross-team maps: Mimica [S1], Soroco [S11] | **Partial/unproven.** `evals/reconstruction`, `server/work_resumption.py`, multi-signal record links and team motifs exist. Family-holdout assignment quality remains below the preregistered ≥0.90 F1 / ≤0.05 contamination gates. Motifs do not prove end-to-end cross-employee cases. | Frozen development/family holdout, independent real annotations, ambiguity/abandonment/multiplexed tabs, interruption rejection, consent/revocation and exact confidence/evidence provenance. Publish failed as well as passed metrics. |
| R2 / P0 | Shared understanding of actual work: Mimica/Soroco [S1,S11] | **Partial.** Aggregated consenting team motifs and caller-reviewed source joins exist. Need source-authoritative case identity, scoped handoffs, per-case contributor provenance and unresolved boundaries. | Two employees/two apps/overlapping cases reconstructed from authorized object IDs; unmatched and conflicting identities remain unresolved; opt-out excludes future sharing and respects deletion. |
| R3 / P0 | Repeatable data ingestion: UiPath/Microsoft/Signavio [S5,S8,S16] | **Partial.** Manual CSV/XES/OCEL and selected Jira/GitHub/Zendesk/Stripe audits; append dedupes supplied exports. Missing complete connector lifecycle for Salesforce/ServiceNow/SAP/M365, checkpoints, schema versions, retries, freshness and revocation. | Real provider sandboxes, least scopes, paginated incremental extraction, late events, deleted records, rate limits, expired grants and retry without loss/duplication; no extraction by default. Coordinate existing #326 connector workstream. |
| R4 / P0 | Governed company analytics: Microsoft/UiPath [S7,S4] | **Missing connected path.** `server/event_log_routes.py` is local; Gateway shares approved process **designs**, not a tenant event-log analysis workspace. Local signals, saved analyses, pipeline and monitoring are separate stores. | Explicitly publish minimized datasets/views; dataset-level roles, field/case minimization, retention/revoke/delete, auditable exports, tenant isolation; two real identities inspect same authorized case and analysis. Preserve local-only mode. |
| R5 / P0 | Connected discovery → implementation: Mimica/Cartographer [S1,S3] | **Partial.** Many cards/APIs, exact catalog revisions and SDK artifacts exist. Pipeline source refs are descriptive strings; no single immutable lineage from log/cohort → model → opportunity → project → run → source receipt. | One journey with exact revision/digest links and coverage; source deletion or revision drift visibly invalidates dependent claims; no automatic approval propagation. |
| R6 / P0 | Trustworthy ingestion/analysis: transformation tooling [S8,S16,S18] | **Partial.** Validation, rejected-row counts, chosen extra columns and explicit timezones exist. Need persistent counts-only quality reports, schema/mapping versions, duplicate/tied-time/completeness diagnostics, quarantine decisions and reproducible transforms. | Bad-data fixture produces reviewable reason counts and sample-free diagnostics; tied order is disclosed; no omitted rows become “complete”; same transform reproduces same artifact hash. |
| R7 / P1 | Fast expert filtering and animation: Disco [S12] | **Partial.** Activity/path sliders, frequency/time overlays, dotted chart and case inspection exist. Missing interactive event-token playback, eventual/direct-follow filter composition and reusable visual filter sets across views. | Play/pause/scrub/speed, tied-time handling and bounded disclosed sampling; filter same full cases across map/variants/KPIs; keyboard/mobile/reduced-motion acceptance. Playback is visualization, never a workflow executor. |
| R8 / P1 | Large interactive analyses: Celonis/Disco [S10,S12] | **Partial/unproven at enterprise scale.** DuckDB 10M-event tests; various distinct limits on case paths, variants, graph size and query memory. Single local workspace lock and synchronous queries are not multi-user service acceptance. | Publish reproducible hardware/event distributions and latency/memory budgets, cancellation, concurrent reads/imports, crash recovery, query-limit disclosure and no partial counts presented as exact. Increase supported scale only after benchmark proof. |
| R9 / P1 | User-defined analysis and data modeling: UiPath/Microsoft [S8,S18] | **Partial.** Typed builder supports bounded group/filter/aggregates, not general PQL/SQL/data transformation parity. Need derived fields, safe expressions, joined business attributes, reusable metrics, units/currencies and lineage. | Typed versioned expression language, division/null/timezone semantics, bounded planner, injection/work-budget tests, identical drill-down and exports; no unrestricted SQL endpoint required. |
| R10 / P1 | Operational monitoring: UiPath/IBM [S5,S13] | **Partial.** Local signals are on demand; no opted-in scheduler, acknowledged alert inbox, lifecycle/deduplication/escalation or authorized external delivery path. | Explicit schedule, rule/dataset version pin, retry and dedupe, stale-data suppression, quiet hours/ack/snooze, auditable recipients and revocation. Signal evaluation must never itself grant business-action permission. |
| R11 / P1 | Governed knowledge discovery: Cartographer [S3] | **Partial.** Confirmed predicates, manual guards, review questions and source checks exist. Missing guided multi-source interview, reconciliation of contradictory policies/SOPs/evidence and statement-level provenance across a richer knowledge model. | Import opted-in documents as untrusted evidence, cite exact statements, expose contradictions, ask only unresolved questions, record a human decision per assertion; malicious document cannot change permissions. |
| R12 / P1 | Process repository and hierarchy: Automation Hub/Cartographer [S3,S20] | **Partial.** Versioned flat catalog, owner/reviewer roles and structural diff/rollback exist. Missing reusable subprocess hierarchy, dependencies and roll-up navigation without duplicating business knowledge. | Version-pinned parent/child references, cycle prevention, permissions on every child, impact analysis and shared edit conflicts; inaccessible children cannot leak aggregate values. |
| R13 / P1 | Full BPMN/business modeling [S3,S4,S16] | **Partial.** `shared/process_model.py` limits 40 activities/100 edges; supported BPMN import intentionally refuses collaboration, triggered events and nested subprocesses. Paired nested parallel ≠ arbitrary BPMN. | Incrementally specify subprocess/call activity, pools/message flow, boundary timer/error, conditional/inclusive flow and multi-instance semantics; preserve unsupported XML as an explicit refusal, never flatten it. Match imports, editor, exports, validators and conformance per supported feature. |
| R14 / P1 | Model-aligned planning/simulation [S4,S14,S15] | **Partial.** Whole-case scenario calibration exists; simulation is bounded path-based with dedicated always-available resource pools and no calendars, breaks, batching or priorities (`server/event_log_simulation.py`). Not simulation of all reviewed BPMN semantics. | Explicit timezone/working calendars, holidays/DST, shared resource pools/queues, seasonal arrivals and branch uncertainty; validate against independent queueing oracles and held-out logs before claiming forecast accuracy. Calendar scope here is a proposed modeling requirement, not a verified feature of every cited vendor. |
| R15 / P1 | Opportunity portfolio governance: Automation Hub [S6,S19,S21] | **Partial.** Local 1,000-item state machine, editable modeled assumptions and history; owner/team assignments, budgets, dependencies, enterprise review stages and access-scoped roll-ups absent. Reassessment of an approved idea currently leaves its approved state unchanged. | Approved assessment changes must require fresh review; versioned assessment history; shared roles/ownership, named decision and exact assessment basis; no review state authorizes execution. Start by fixing approval drift in the existing pipeline. |
| R16 / P1 | Realized value and benefit tracking: UiPath/Soroco [S19,S21,S11] | **Missing proof/product depth.** Scenario value is user-modeled; implemented state is manual; execution reports do not prove employee effort saved or realized financial return. | Versioned baseline and intervention, comparable periods/cohorts, authorized outcome receipts, error/review/runtime costs, adoption and uncertainty; distinguish actual, modeled and self-reported values; prevent double counting overlapping automations. |
| R17 / P1 | Implementation from real examples: Mimica Maker [S1] | **Partial/unproven.** Runnable Agents SDK path has real synthetic transport tests; Codex/Claude/n8n/AI SDK starters are not all equivalent deployable implementations. Business-specific mappings and held-out customer cases remain. | At least one customer-controlled runtime receives a pinned approved model, explicit current tool mappings and current intent; generated tests cover normal/exception cases; build, sandbox run, independent readback, rollback/kill and retry outcomes recorded. Expand runtime support with equivalent tests. |
| R18 / P1 | Continuous run lifecycle: IBM/Celonis [S10,S13] | **Partial.** Journal projection/import, graph conformance, bounded source checks and drift proposals exist. Missing reliable runtime adapters, deployment/version inventory, incident triage and automated receipt association under separate grants. | Signed/authenticated provider-specific reports, artifact/case lineage, replay/tamper tests, version drift, lost reports, duplicate receipt handling, review/rollback without silently replaying a business write. |
| R19 / P1 | Predictive/prescriptive monitoring: ABBYY/IBM [S13,S14] | **Partial/unproven.** Prefix-comparable remaining-time/risk estimates and temporal backtesting exist. Need production-label ingestion, censored/open-case bias handling, drift/calibration monitoring and reviewed intervention testing. | Held-out temporal metrics and subgroup support, censoring/survival semantics, abstention, recalibration proposal, effect of interventions separately tested; correlation must not become prescriptive causal advice. |
| R20 / P1 | Company-ready administration [S7,S16] | **Partial.** OIDC, scoped process review, enrollment/offboarding, consent, fleet recorder/sync diagnostics exist. No SCIM (`docs/ORGANIZATION_ROLLOUT.md`); business-connector health remains distinct/unchecked. | Provision/update/disable lifecycle, role inheritance and revoked access, connector/job health, backup/restore, upgrade rollback and self-hosted deployment runbook; a healthy recorder cannot imply a healthy source connector. |
| R21 / P2 | Rich decision knowledge: Cartographer [S3] | **Partial.** Text/structured rules with confirmed predicates are not decision tables, reusable rule sets, decision dependencies or full policy-effective dating. | Human-approved decision tables with boundary/overlap/gap tests, effective dates and exact source basis; generated planner respects stale/unknown rules. Evaluate DMN interchange rather than inventing incompatible execution semantics. |
| R22 / P2 | Communications AI: UiPath ecosystem / broader intelligence | **Partial; scope expansion proposal.** `shared/communications_mining.py` uses a user-editable keyword taxonomy; messages are discarded and aggregates retained. No calibrated semantic multi-label learning, annotation lifecycle or multilingual quality proof. | Separately consented, retention-bounded review examples, independent multilingual holdout and per-intent uncertainty; imported email is never instructions. Current research does not certify a specific vendor's communications accuracy. |
| R23 / P2 | Domain apps/templates: UiPath/IBM/Signavio [S4,S13,S16] | **Missing integrated packs.** Generic components and selected source examples exist; no complete reusable P2P/O2C/service-management package with schema, definitions, KPIs and validation. | Ship one source-validated pack with mapping/versioned KPI definitions, fixtures, install/upgrade and explicit assumptions; measure setup time with a new implementer. |
| R24 / P2 | Object-centric depth: Celonis [S10] | **Partial.** OCEL import/export and per-object maps exist. Need stronger temporal object attributes, qualifiers across changing relationships, object-centric conformance and reusable event/object business model. | Many-to-many purchase/order/invoice/payment scenarios with independently checked relationship counts and temporal semantics; no duplicate-case benefit/cycle-time claims. OCPM is not complete merely because OCEL parses. |
| R25 / P0 proof | All vendors' usable-product claims | **Unproven.** No representative consenting multi-team pilot or independent competitor-style UX acceptance; synthetic transport/native CI and millions of events do not establish adoption or business value. | Run the real 2–4-week pilot from #326 E, independently annotate 20–30 real sessions, test company onboarding and representative tasks, publish failure inventory and paired value/outcome results. |

## Delivery plan and concrete acceptance

These are work packages, not estimates or certified delivery dates. Follow the
existing architecture; do not build a second evidence collector, process model,
opportunity database or proprietary robot runtime.

### M0 — Review integrity and a connected user journey (P0)

- [ ] Fix R15 approval drift: after reassessment, an approved idea needs a fresh
  assessment review. Preserve the previous assessment and state change in bounded
  history; stale concurrent updates still fail. Cover HTTP and actual browser flow.
- [ ] Define and implement R5 lineage using existing process refs/digests,
  log/cohort refs, idea IDs, project spec hashes, execution IDs and receipt refs.
  Add optional references compatibly; validate tenant/scope on resolution. Never
  treat a user-supplied reference string as verified identity.
- [ ] Add one evidence → reviewed design → idea → sandbox project → report journey,
  with explicit human decisions at the existing boundaries. Cross-view navigation
  must retain the exact cohort and model revision and show unavailable evidence.

Exit: a new analyst can complete that journey on a supplied realistic authorized
fixture, reopen it after restart, and see which claims remain unverified.

### M1 — Reliable data plane (P0; R3/R6)

- [ ] Add persistent minimized quality/mapping reports to existing log metadata:
  accepted/rejected/duplicate events, tied timestamps, missing labels/resources,
  declared case notion, mapping/schema version and transform digest. Separate
  missing-field counts from unknown historical completeness.
- [ ] Define a connector contract: provider/account identity, declared case keys,
  checkpoints/watermarks, late-event policy, atomic append, grant state and safe
  counts-only job receipts. Reuse append/dedupe rather than another event store.
- [ ] Coordinate and finish the existing enterprise-source connector workstream;
  require actual sandbox acceptance before checking it complete.

Exit: recover from rate limiting and a crashed incremental job without dropped or
silently duplicated events; disabling the grant stops new reads; retained data
follows the explicitly chosen retention/deletion policy.

### M2 — Shared analysis workspace (P0; R4/R8/R20)

- [ ] Extend the Gateway's existing tenant/identity/role/audit model to explicitly
  shared minimized datasets and analysis artifacts. Keep local-only default.
- [ ] Add job/cancel/status APIs and bounded query budgets, versioned dataset
  snapshots, viewer/editor/owner controls and auditable exports.
- [ ] Persist and share chosen analyses/signals/pipeline entries against exact
  dataset/process refs; test page, field and case-level disclosure boundaries.

Exit: two consenting roles share the same authorized analysis; another tenant
and a revoked viewer cannot resolve it or derive hidden aggregate counts.

### M3 — Reconstruction accuracy (P0; R1/R2)

- [ ] Keep the preregistered holdout sealed during development. Compare explicit
  evidence signals and an optional authorized semantic linker on development data.
- [ ] Present uncertain membership/handoff suggestions for correction without
  rewriting canonical events. Retain observed/source-linked/human-confirmed edges.
- [ ] Run the frozen holdout once selected, then independent annotated real
  sessions. Record model/settings/version/cost and false joins, not F1 alone.

Exit: ≥0.90 assignment F1 and ≤0.05 contamination with interruption rejection
preserved, plus real-session evidence. If not reached, publish the miss and keep
uncertainty visible. Provider credentials and real annotations are external
prerequisites, not something to fabricate or silently replace with fixtures.

### M4 — Process knowledge and model depth (P1; R11/R12/R13/R21)

- [ ] Guided clarification with source citations, conflicting assertions and
  human-approved decision records on the existing catalog.
- [ ] Version-pinned subprocess hierarchy/dependencies with access-aware roll-ups.
- [ ] Add BPMN semantics one feature at a time with matching import/editor/export,
  validation and conformance; make larger graph support performance-backed.

Exit: a changed child process or rule invalidates the affected approval/build
basis; unsupported constructs fail visibly; historical exports stay reproducible.

### M5 — Analysis and simulation depth (P1; R7/R9/R14/R19/R24)

- [ ] Reusable filter/metric definitions and typed derived fields with exact
  drill-down; explicit units/currency and scope.
- [ ] Calendar/shared-pool simulations and model-aware calibration validated by
  independent queueing examples; predictions disclose uncertainty and censoring.
- [ ] Playback and object-centric diagnostics preserve exact sample/coverage flags.

Exit: benchmarked representative workloads, no silent truncation or causal claims,
consistent results across API, diagram, table and exports.

### M6 — Operational loop and realized value (P1; R10/R16/R17/R18)

- [ ] Opted-in schedules and acknowledged signal inbox with dataset freshness,
  idempotency and explicit delivery recipients. No automatic business-action grant.
- [ ] Pinned customer-runtime adapters, authorized sandbox tests, deployment
  inventory and independently associated outcome receipts.
- [ ] Baseline/intervention/actual-versus-modeled benefit records with supporting
  outcome/effort observations, costs, uncertainty and overlap-aware roll-ups.

Exit: one actual authorized workflow runs normal and exceptional cases on a real
customer sandbox; retry and version/revocation failure stop safely; verified
business outcomes and reported/modelled savings remain separate.

### M7 — Product acceptance and domain expansion (R20/R22/R23/R25)

- [ ] Complete multi-team pilot and independent UX task review; record setup time,
  task completion, errors and confusing interpretation boundaries.
- [ ] Harden identity provisioning, backup/restore/upgrade and source diagnostics.
- [ ] Add a source-validated domain pack; semantic communications improvements only
  with separate consent and multilingual held-out evaluation.

Exit: publish the pilot report and unresolved failures. Do not declare full parity
because a checklist, test count or model-generated diagram is complete.

## Architecture and non-negotiable boundaries

Use the existing catalog as the persistent versioned process knowledge model.
Connect imported events/objects, canonical evidence and record identities through
references with explicit scope and origin. Use immutable versioned derived
artifacts; keep process review, observation consent, data sharing, tool grants,
current business intent and verified outcomes separate. Derived artifacts must
not outlive permissions invisibly. Unknown data stays unknown; missing events do
not prove conformance, productivity, savings or a correct business result.

Functional parity includes useful competitor capabilities. It does not require
copying proprietary source, names, images, layouts or broadening ordinary capture
to typed content. Support customer runtimes with explicit grants rather than
replaying historical clicks or building a second RPA engine by default. Any future
consequential action remains behind the existing current-intent, authorization,
precondition, independent-readback and replay-prevention checks.

## Validation and evidence required for each package

- Appropriate unit/property tests for semantics and real browser acceptance for
  changed user flows; keyboard, mobile, WCAG and minimized safe rendering.
- Native supported-OS CI and packaged install/start/export compatibility.
- SQLite/PostgreSQL tenant isolation and revocation where shared stores change.
- Representative declared scale and work-budget tests; unknown and refused
  computations explicitly counted. Do not benchmark only uniform happy paths.
- Exact commit/tree/artifact digests in evidence; failures remain visible.
- Separate synthetic, provider-sandbox and real pilot evidence. No mocked provider
  test closes an actual provider acceptance gate.

## Primary sources

Retrieved 2026-10-24. **Guide** = vendor documentation; **product** = vendor claim.
These links support the feature claims above, not independent certification.

| ID | Source | Evidence used |
| --- | --- | --- |
| S1 | [Mimica product](https://www.mimica.ai/product) — product | Miner opportunity discovery; Mapper decision/exception/variant maps and time/cost/application measures; Maker agent code tested on real examples; PDD/BPMN/CSV exports. |
| S2 | [UiPath Task Mining introduction](https://docs.uipath.com/task-mining/automation-cloud/latest/user-guide/introduction-as) — guide | Known-task trace clustering/merging, decision points and PDD/XAML; explicit exclusions for unknown-task discovery and ranked recommendations. |
| S3 | [UiPath Cartographer](https://www.uipath.com/product/cartographer) — product | Guided conversation, reconciled sources, approved sourced facts, OKF Map of Work, objects/rules/exceptions, ownership/versioning and PDD/SDD. |
| S4 | [UiPath Process Mining introduction](https://docs.uipath.com/process-mining/automation-cloud/latest/user-guide/introduction-to-process-mining) — guide | System-of-record discovery, analysis and process apps. |
| S5 | [UiPath scheduling data runs](https://docs.uipath.com/process-mining/automation-cloud/latest/user-guide/scheduling-data-runs) — guide | Extraction scheduling using CData Sync or customer Windows task scheduling. |
| S6 | [UiPath Automation Hub introduction](https://docs.uipath.com/automation-hub/automation-cloud/latest/user-guide/introduction) — guide | Collaborative identification, pipeline and documentation repository. |
| S7 | [Microsoft sharing processes](https://learn.microsoft.com/en-us/power-automate/process-mining-share) — guide | Owner/co-owner/viewer analysis roles; also explicit limitations including no Entra-group sharing. |
| S8 | [Microsoft editing and refreshing processes](https://learn.microsoft.com/en-us/power-automate/process-mining-data-source) — guide | Power Query transforms, scheduled/incremental refresh and externally triggered refresh; current documented scheduled maximum once daily. |
| S9 | [Microsoft process mining overview](https://learn.microsoft.com/en-us/power-automate/process-mining-overview) — guide | System-of-record analysis and Microsoft ecosystem integration. |
| S10 | [Celonis platform](https://www.celonis.com/platform) — product | Data Core/billion-record claim, connected context model, analysis/design/operation, simulation and enterprise AI orchestration. |
| S11 | [Soroco Scout for business](https://soroco.com/scout-business/) — product | Work understanding, contextual simulation, AI/automation opportunities and intervention-benefit tracking. |
| S12 | [Fluxicon Disco](https://fluxicon.com/disco/) — product with detailed feature list | Process map abstraction, animation/time controls, complete case histories and direct/eventual-follower filtering. |
| S13 | [IBM Process Mining](https://www.ibm.com/products/process-mining) — product | Digital twins, predictive/prescriptive monitoring, corrective actions, automation and domain integrations. |
| S14 | [ABBYY Timeline](https://www.abbyy.com/timeline/) — product | Discovery, analysis, monitoring, prediction and simulation. |
| S15 | [ABBYY process simulation](https://www.abbyy.com/timeline/process-simulation/) — product | Virtual future-state testing and capacity/change scenarios. |
| S16 | [SAP Signavio Process Intelligence](https://www.signavio.com/products/process-intelligence/) — product | Extraction, data pipelines/quality, live insights, conformance, simulation, out-of-box analyses and actionability. |
| S17 | [Apromore](https://apromore.com/) — product | Integrated mining/modeling/simulation/monitoring positioning; not a source for unsupported feature-level or accuracy claims. |
| S18 | [UiPath data transformations](https://docs.uipath.com/process-mining/automation-cloud/latest/user-guide/data-transformations) — guide | SQL table transformations, editor and preview over ingested data. |
| S19 | [UiPath cost-benefit analysis](https://docs.uipath.com/automation-hub/automation-cloud/latest/user-guide/performing-the-idea-cost-benefit-analysis) — guide | Baseline versus actual/new estimate, phase costs, running costs and benefit refinement. |
| S20 | [UiPath process repository](https://docs.uipath.com/automation-hub/automation-cloud/latest/user-guide/about-the-process-repository) — guide | Business processes, linked solutions and subprocess hierarchy. |
| S21 | [UiPath rolled-up benefit](https://docs.uipath.com/automation-hub/automation-cloud/latest/user-guide/rolled-up-benefit) — guide | Benefit totals over linked automations and access-aware withholding of incomplete totals. |

Research exclusions: some initial guessed URLs returned 404, some UiPath pages
redirected to fallback topics, Celonis documentation returned 401, and SAP's main
product endpoint returned 403. We used the successful canonical pages above,
not error pages, navigation-only entries, or inaccessible documentation, as
feature evidence. No authentication bypass was attempted.
