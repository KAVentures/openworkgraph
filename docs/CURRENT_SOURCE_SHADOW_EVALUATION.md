# Current-source shadow evaluation

Generated SDK projects can compare proposed routes with customer-reviewed
expectations using the current-source invariant bindings already supported by
planning and execution. This closes the structured-rule gap in shadow testing
without authorizing business actions.

```sh
python evaluate_sources.py --cases reviewed-cases.json --bindings bindings.json \
  --model MODEL --output new-report.json \
  --confirm-source-reads --confirm-model-rule-check-sharing
```

Use the existing `openworkgraph.reviewed-shadow-cases.v1` suite: exact process
reference and specification hash, `reviewed_by_customer: true`,
`case_inputs_authorized: true`, and cases with opaque IDs, declared categories,
authorized `input` objects, and individually confirmed expected routes or
clarifications. Follow the generated evaluation tests for a complete example.
The output must be a new file. Exit status 0 means all reviewed expectations
matched; 2 means some require review. Errors or batch cancellation write no
partial report.

Install the generated requirements in an isolated environment. Configure the
separate local source-read credential and exact-revision, reviewed
[source-rule bindings](RUNTIME_SOURCE_RULE_BINDINGS.md). Shared exports also
require the organization catalog-read credential. Current independent design
review is checked before and after each proposal. A lost review aborts the
batch. Source access still requires existing local opt-ins, selected canonical
records and AI/history grants; this command changes none of those settings.

Both confirmations precede any source or model calls. As in
[source-aware planning](SOURCE_AWARE_PROCESS_PLANNING.md), the selected model
receives the saved process, case inputs and bounded rule IDs/match statuses.
Expected routes, case review declarations and evaluation categories are not
added to its context. Match statuses can reveal information about declared
conditions; case inputs themselves can contain references or private data, so
review both transfers explicitly. Source credentials, raw receipts, source
values and provider errors are not added to model context.

The entire suite and expected routes are checked before starting source reads.
Select at most eight cases and eight distinct reference/type pairs per case;
the whole batch has a five-minute deadline. Every case requires at least one
bound invariant and every expected proposal must bind all structured rules it
uses. Unconfirmed rules and manual guards remain blockers. A missing selected
reference is an input error; to test unavailable source facts, select a valid
reference whose authorized lookup cannot establish fresh state.

Each case reads its selected records once, retaining receipts only in memory.
The planner and independent comparison recheck freshness and used predicates.
False, unknown, wrong-record, stale or unsupported facts cannot justify a
proposal. Unavailable state can correctly produce an expected clarification.
The receipts are discarded after that case's comparison. Comparisons describe
each case's own read time: they are not a single atomic batch snapshot and do
not establish historical case truth, correct case-to-record association,
business policy, representative population coverage or business outcomes.

`new-report.json` uses the existing minimized
`openworkgraph.shadow-evaluation.v1` format. It includes exact specification,
suite and result hashes, case hashes, counts and bounded comparison reasons.
It contains no inputs, raw source state or model prose. Inspect it through the
existing local/shared process shadow-review flow. These reports remain
customer-supplied assertions; importing a report does not authenticate its
source reads or expectations. The report's `business_rules_evaluated: false`
continues to mean that complete business policy has not been established.
The printed summary identifies the narrower current-invariant evaluation basis.

Normal offline `evaluate.py` cannot use imported statuses to bypass structured
rules. Only the live driver supplies pinned in-memory receipt contexts. This
mode registers no business tools, creates no execution intent, verifies no
source outcomes and changes no process model. A later separately approved
execution still performs its own fresh source and review checks.

Verification uses the actual SDK Runner with in-memory models, owned synthetic
receipts, and synthetic authenticated HTTP source/catalog services. It does not
certify live customer connectors, model providers or production outcomes.
