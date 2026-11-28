# Evidence-pinned candidate selection and held-out review (R26)

Use two separately reviewed trial populations of the exact same saved process.
The discovery suite is used to nominate a candidate; the holdout suite is used
for the independent-in-design confirmation stage. Both plans must keep the
same candidate IDs, configuration hashes, baseline, currency and trial gates.
Input hashes must be unique within each suite and disjoint across suites even
if their opaque case IDs differ. This detects exact input reuse, not semantic
overlap or proof of independent sampling. Keep holdout cases and their answers
away from the nomination decision and candidate development.

Use the existing trial/replay/loopback collector to obtain minimized capture
ledgers. These remain customer-supplied evidence, not attested performance.
This selection tool recomputes the complete trial review from the frozen plans
and captures; arbitrary supplied summary metrics are not accepted.

## Nominate before inspecting holdout results

Create a JSON bundle with precisely discovery_plan, discovery_captures,
holdout_plan and request. The first three contain the actual JSON objects
from the trial artifacts. Request example:

```json
{
  "candidate_id": "alternative",
  "nominator_alias": "reviewer-a",
  "confirmed": true,
  "policy": {
    "minimum_discovery_cases": 5,
    "minimum_holdout_cases": 5,
    "minimum_repeats": 2,
    "minimum_candidate_match_basis_points": 9000,
    "minimum_paired_match_change_basis_points": 0,
    "maximum_regression_basis_points": 0,
    "maximum_candidate_cost_ratio_basis_points": 10000,
    "maximum_candidate_p95_ratio_basis_points": 12000,
    "require_complete_cost_latency": true,
    "require_all_categories": true,
    "require_distinct_reviewer_alias": true
  }
}
```

A basis point is 0.01 percentage points; a ratio of 10000 means the candidate
must cost no more than the baseline. Optional ratio gates default to absent.
All decisions require complete outcomes for the selected candidate and baseline,
zero candidate critical attempts/non-delivery, passing trial gates and the
frozen comparison policy. Missing outcome/telemetry evidence is never
interpreted as success. Ratio checks use integer multiplication, including
a zero-cost or zero-latency baseline. The nomination pins the discovery
review and exact holdout plan, candidate, and policy; it refuses discovery
gate failures or undersized holdout plans. It receives no holdout captures.

```sh
python -m shared.agent_procurement_selection nominate --process-export process.json --bundle nomination-input.json --out nomination.json
```

Retain nomination.json and its digest before seeing holdout outcomes. The
artifact has no independent timestamp or signature; an external customer
governance system must establish actual chronology and identity.

## Assess and decide

Assessment bundle: discovery_plan, discovery_captures, holdout_plan,
holdout_captures, nomination. Supply the exact original nomination. The
tool recomputes discovery and rejects changed inputs, then applies the
same policy to the held-out comparison.

```sh
python -m shared.agent_procurement_selection assess --process-export process.json --bundle assessment-input.json --out assessment.json
```

Inspect every required check; a fail or unknown blocks selection. Add request
to the assessment bundle for a separate explicitly confirmed decision:

```json
{
  "decision": "select_for_substitution_rehearsal",
  "reviewer_alias": "reviewer-b",
  "expected_assessment_sha256": "REPLACE_WITH_ASSESSMENT_SHA256",
  "expected_nomination_sha256": "REPLACE_WITH_NOMINATION_SHA256",
  "confirmed": true
}
```

Other dispositions: retain_baseline or defer, including when holdout fails.
Distinct aliases are required by default, but aliases do not establish
separate real people or organizational authority. Recalculation must match
both pins before a decision receipt is created.

```sh
python -m shared.agent_procurement_selection decide --process-export process.json --bundle decision-input.json --out receipt.json
```

The local Process catalog also supports these three operations for a saved
current revision. Historical revisions cannot issue a fresh nomination or
decision through the dashboard. The ephemeral result can be downloaded; it
does not persist a selection or change a deployment. CLI files are private
exclusive outputs on POSIX; Windows inherits ACLs. Inputs are bounded at
8 MiB; use smaller trials if a combined bundle exceeds this limit.

A selected candidate is only a nominee for a separately controlled
substitution rehearsal, not a selected production deployment. Remaining
requirements include exact runtime/schema compatibility, controlled rehearsal,
independent source-outcome evidence and separately authorized rollout and
rollback. No external model, source or business action is invoked. Hashes are
content identifiers, not authentication, anonymization or proof of independence.
