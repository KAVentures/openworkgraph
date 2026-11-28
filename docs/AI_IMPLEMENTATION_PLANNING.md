# Pre-implementation planning for a reviewed proposal batch (P0 #1663)

Compare candidate scenarios before committing to implementation. This extends
the existing multi-proposal review (#1688/#1689), not the existing saved-idea
portfolio optimizer or post-test operating-cost evaluator. The external AI and
the reviewer supply business judgments; OWG performs exact arithmetic and
maintains evidence lineage. No new ranking or solver, model calls, writes to the
portfolio, permissions, or execution.

## Prepare a complete planning worklist

Retain the original evidence files, returned proposal array, original packet
pin and independently retained batch-review pin. Specify one currency for
all assumptions (no conversions are performed).

```sh
python -m shared.ai_implementation_planning worklist \
  --source original-evidence.json --packet-sha256 ORIGINAL_PACKET_PIN \
  --proposals returned-proposals.json --batch-review-sha256 EXACT_REVIEW_PIN \
  --currency SEK --output planning-worklist.json --confirm-local
```

The worklist contains each distinct structurally reviewed proposal index and
digest, initially unassessed. Do not remove any row or change the identifiers.
Rejected and duplicate items remain in the final report. Leave an assessment
null if not reviewed. For a reviewed candidate, replace its assessment with
a declaration such as the following *illustrative assumptions only*:

```json
{
  "ranges": {
    "cases_per_month": ["60", "120"],
    "eligible_share": ["0.5", "1"],
    "net_human_minutes_saved_per_eligible_case": ["-6", "12"],
    "human_capacity_value_per_hour": ["100", "200"],
    "incremental_operating_cost_per_month": ["10", "100"],
    "one_time_implementation_cost": ["100", "200"]
  },
  "feasibility": "needs_review",
  "risk": "unknown",
  "depends_on_item_indexes": [],
  "basis_note": "Hypothetical bounds; obtain representative workload and cost measurements before adopting."
}
```

Ranges are exact decimal strings, low then high, or null if unknown.
Net human minutes must include review, supervision, exception handling,
fallback, rework and escalation, and may be negative. Include all
incremental ongoing AI, tools, infrastructure, and maintenance costs in the
operating range; avoid counting the same cost as both person-time and direct
expense. First-year figures assume twelve identical operating months, full
declared eligibility and the stated upfront implementation cost, not a rollout
schedule. Capacity valuation is not cash flow. Feasibility and risk are
human-reviewed declarations, not verified permissions, safety or readiness.
Use plausible, needs_review, blocked, or unknown for feasibility; low, medium,
high, or unknown for risk. Dependencies use zero-based distinct reviewed item
indexes and must be acyclic. The basis note should explain input provenance,
uncertainty, quality/adoption requirements and external dependencies.

After independent human review, set confirm_human_reviewed_assumptions to true.

```sh
python -m shared.ai_implementation_planning compare \
  --source original-evidence.json --packet-sha256 ORIGINAL_PACKET_PIN \
  --proposals returned-proposals.json --batch-review-sha256 EXACT_REVIEW_PIN \
  --plan planning-worklist.json --output planning-review.json --confirm-local
```

## Interpretation

The report recomputes the original batch and verifies all proposal pins,
preserves every item disposition and unassessed candidate, and gives exact
rational lower/upper bounds for modeled net human hours, capacity value,
capacity value less incremental operating cost, and first-year capacity value
less incremental costs. Values are fraction strings (e.g. 1/60), not
probabilities or measured forecasts. Unknown inputs propagate to dependent
metrics. Each scenario is conditional and independent; no portfolio total is
computed. Shared scoped event citations flag potential overlaps, but disjoint
citations do not establish disjoint work, budgets, or resources. Missing or
non-plausible dependency assessments are visible; this does not establish
dependency readiness or produce a schedule. The report retains original coverage
gaps and exact input/report pins.

Review the output with the external AI and decision-makers, then use the
existing individual idea assessment and planning workflow, with separate
approval and the existing downstream portfolio solver. Do not adopt modeled
benefits as actual savings or as evidence that an AI system meets its
quality, safety, and operational requirements. The separate retained
outcome/operating-cost evaluation is necessary after testing.

The same planning workflow is available in the local implementation dashboard after reviewing a proposal batch. Expand **Compare implementation economics and dependencies**, enter the currency, prepare a worklist, and enable the candidates you wish to assess. Complete both bounds or leave both blank for unknown. Use displayed one-based item numbers for dependencies; the editor converts these to the original zero-based indexes. Confirm the assumptions explicitly, compare scenarios, and download the exact reviewed assumptions and report. Source, budget, batch or currency changes clear the worklist; editing assumptions clears the result and confirmation. Unassessed and invalid candidates remain visible. This does not save assessments or rank the portfolio. Mapping reviewed assumptions to saved assessments remains follow-up. The CLI remains available.
No live-data queries or external requests occur; outputs are exclusive
private bounded files.

## Validation

```sh
python -m pytest -q tests/test_ai_implementation_review.py tests/test_ai_implementation_batch.py tests/test_ai_implementation_planning.py
```

## Reconcile a reviewed scenario with the saved portfolio

After a successful planning comparison, select **Reconcile with saved portfolio**. This read-only request recomputes the original batch and planning pins, then reads the current saved local idea inventory. It matches only retained AI-proposal lineage (original packet, proposal, structural review, source reference and preview digest presence), not titles or manually typed source text. Missing, ambiguous and conflicting matches, refused items, duplicate items and unassessed candidates stay visible. A match shows the saved idea ID, revision, state and budget; the saved budget is compared to the candidate's declared one-time cost interval only in the same currency. Resolved dependency links, missing links, external saved links and unresolved candidates are shown separately. External saved links may be legitimate. Net human hours in the planning scenario are not the existing pipeline's gross eligible hours. No assessment is automatically copied or approved and the optimizer is not invoked. Download the canonical reconciliation report if needed; changes to the scenario invalidate it. Refresh the snapshot and independently review saved revisions before using the existing portfolio controls. The endpoint does not authorize actions or assert actual cost, feasibility or savings.

## Explicit handoff of one reviewed candidate to the saved portfolio

After a fresh reconciliation, expand **Transfer reviewed planning to one saved idea**. Select one editable saved idea (idea, assessing or on hold) with matching retained lineage and a reviewed scenario. OWG recomputes the original batch, planning and reconciliation pins. The candidate must have plausible *declared* feasibility, and its scenario dependencies must resolve to nonrejected saved ideas with plausible declared assessments. This does not establish operational readiness. The preview preserves the saved owner, team, target quarter, existing budget and unrelated dependency links, adding only resolved scenario links. Optionally enter an exact decimal implementation-budget estimate within the reviewed one-time cost interval and scenario currency, only when no saved budget exists. No approved budget is created or replaced. A blank estimate preserves the current budget. Review the exact previous/proposed planning and revision, then explicitly confirm. The save endpoint recomputes the preview and calls the existing planning operation under the same process lock, preserving its revision and dependency-cycle checks. It does not change an assessment, decision state, approval or execution permission. Approved, implemented and rejected target ideas are refused. Refresh the reconciliation and portfolio before further decisions. The lock coordinates existing writers in this server process; no additional cross-process transaction guarantee is claimed. If the save response is lost, inspect the current saved revision instead of retrying automatically. No model or business source is contacted.

The new focused route tests substitute the portfolio persistence operation and local authentication; the separate isolated browser smoke uses these same substituted components. Full integrated persisted-portfolio and real authorization tests remain necessary.

## Compare an implementation forecast with retained results

For an implemented idea with exact original proposal lineage and a reviewed scenario, refresh the portfolio reconciliation. Use the existing saved idea **quality and adoption** panel to calculate a before/after scenario over retained effort periods, and download its **Scenario JSON**. In the planning panel, expand **Compare implementation forecast with retained results**, select the matching implemented candidate, attach that original quality-benefit export (256 KiB maximum), and explicitly review each comparability dimension. Enter a basis note; leave comparisons unchecked where the workload, eligibility, net human effort, nonlabor cost, valuation or upfront cost definitions are not comparable. There is no automatic comparability assumption.

The local API recomputes the original batch and planning review, exact portfolio lineage and revision, and the original quality-benefit engine's result from its retained measurement snapshot and declared assumptions. It refuses altered values, stale snapshots and mismatched ideas. JSON integer/float representations of the same exact numeric value are equivalent for browser exports; booleans are not numbers. The server pins its recomputed result. The report shows the reviewed range and reported value, below/within/above the range where scope is confirmed, and unknown, unresolved comparability or currency mismatches otherwise. Net effort comparisons require workload, eligibility and effort comparability. Monetary comparisons require the same currency. Incremental nonlabor cost is the negative of the existing baseline-minus-after cost delta, normalized to after volume. Upfront cost and hourly value are reviewer declarations, not independently verified actual costs. Quality and adoption gates remain separate, as do whole-population uncertainty and excluded measurement IDs. Changes to the plan, portfolio snapshot, export or comparability invalidate the displayed report. Download the canonical comparison for local retention.

The comparison uses reported rounded values from the existing quality-benefit engine, so boundary classifications may be sensitive to display precision. It does not verify that the forecast was recorded prospectively, that retained periods represent the entire workflow, or that reported changes were caused by AI. It does not verify business outcomes, realized savings or cash flow, invoke an optimizer, authorize rollout or write to the portfolio. No new store, model calls or data uploads occur. The exact original forecast and quality report remain necessary for independent review.

## Close the feedback loop with an externally proposed revision

After a successful forecast review, select **Prepare improvement feedback**. The server recomputes the original review against the current retained quality-benefit result, rather than trusting an uploaded forecast report. The bounded canonical feedback packet includes the exact original proposal and forecast/packet pins, metric comparisons, declared comparability dimensions, quality and adoption gates, selected/excluded measurement counts and coverage gaps. It omits unrelated proposals, exact measurement IDs, saved idea IDs, comparability notes and raw event logs from the feedback wrapper. The original proposal and scoped evidence references may still contain sensitive content; this is not redaction or sharing authorization. Review the file locally and separately decide whether to provide it to an authorized external AI alongside the original prepared evidence and its instructions. No external call or automatic upload occurs.

The feedback instructions ask the external AI to prioritize quality and uncertainty, consider local/cloud, hybrid, deterministic and human alternatives, preserve policy and unresolved questions, propose held-out testing, escalation and rollback, and return a single revised existing v1 implementation proposal citing only the original scoped evidence. Operational feedback is not new event evidence. If further evidence is needed, obtain and review it separately rather than inventing references.

Select the returned **Revised proposal JSON** and choose **Review proposed revision**. The server recomputes the current forecast and exact feedback digest, then invokes the existing structural proposal reviewer on the original evidence. It refuses unchanged substantive fields, stale pins or fabricated citations. The revision review records exact proposal digests, changed structural fields and counts of removed questions and approval requirements. Counts and field changes are navigation aids, not proof of preserved policies or improvement. Download the canonical review and retain the original revision file. Use the existing individual proposal review, shadow testing and approval workflow for any subsequent implementation decision. No workflow, budget, approval, saved knowledge or runtime is changed. Edits to the forecast invalidate feedback and revision receipts; changing the revision file invalidates its receipt. A stale retained measurement or idea revision requires a new forecast review.
