# Package-pinned native shadow test roster (P0 #1663)

This tool connects the staged shadow skill manifest (#1670/#1678) to the
existing native trace review (#1672/#1674) before conducting the test.
The reviewer must retain all planned cases, not only convenient runs.

## Prepare before the test

Extract and verify the actual skill package using the existing
`shared.ai_skill_stage verify` procedure. Keep its independently recorded
package SHA-256. Prepare a private cases.json array, for example:

```json
[
  {"case_id":"case_normal","expected_framework":"claude_code","reviewed_tool_labels":["Read"]},
  {"case_id":"case_exception","expected_framework":"claude_code","reviewed_tool_labels":[]}
]
```

Choose the held-out cases and independently restricted test environment
before running any agent. Opaque case identifiers refer to separately
retained test inputs; this roster does not define or grade their outcomes.
Exact expected framework and tool display labels are declarations, not
permissions. The roster supports one to fifty distinct cases.

```sh
python -m shared.native_shadow_roster freeze \
  --manifest extracted-skill/manifest.json --package-sha256 EXACT_PACKAGE_PIN \
  --cases cases.json --output plan.json --confirm-local
```

Record plan.json's plan_sha256 independently before collecting results.
This verifies manifest content and copies the original packet, proposal,
draft and package pins; it does not verify installed files, identity,
enrollment timing, or whether the skill was used.

Generate the full blank assignment worklist:

```sh
python -m shared.native_shadow_roster worklist \
  --plan plan.json --plan-sha256 EXACT_PLAN_PIN \
  --output assignments.json --confirm-local
```

Run the cases manually in the independently restricted shadow environment.
For each planned case, record the actual root execution ID or leave null.
Do not delete cases, reuse roots, or derive expectations from observed runs.
Retain the original trace snapshot through the existing Agents dashboard.
Reconcile the exact plan and trace after the test:

```sh
python -m shared.native_shadow_roster review \
  --plan plan.json --plan-sha256 EXACT_PLAN_PIN \
  --trace native-shadow-trace.json --trace-sha256 EXACT_TRACE_PIN \
  --assignments assignments.json --output roster-review.json --confirm-local
```

The report accounts for every planned case: unassigned, root not returned,
incomplete observation, observed deviation/failure, or no deviation in
returned events. It recomputes the existing trace review for assigned
roots, retains evidence gaps, identifies unassigned returned executions,
and pins all inputs. Empty trace inventories do not establish success.
No case is a verified business success solely because a trace looks clean.
An independent held-out outcome assessment and actual permission and
package-invocation verification remain necessary before a separate
production rollout decision.

The original plan, manifest, assignments, trace and report should remain
private. Outputs are exclusive, bounded local files. No data uploads, new
stores, model calls, source writes or authorization occur. This is a local
CLI capability; dashboard roster integration is a follow-up.

## Validation

```sh
python -m pytest -q tests/test_native_shadow_roster.py tests/test_native_shadow_trace_review.py
```

Focused tests exercise the actual existing trace reviewer, exact pins,
all five dispositions in one frozen population, missing cases, root and
child reuse, malformed declarations, tampering and exclusive outputs.
