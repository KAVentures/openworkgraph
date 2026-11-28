# Review retained native shadow attempt traces (P0 #1663)

This local tool connects customer-declared shadow cases and staged skill
packages to the existing native agent structural trace evidence, without
introducing a new runtime or telemetry store. It complements native
skill staging (#1670) and the existing evaluation and outcome systems.
It does not launch a model.

## Capture and review

Use an isolated, independently restricted test environment with the
existing supported native agent adapter. See NATIVE_AGENT_ADAPTERS.md.
Stage and verify the skill package separately; configure actual tool
permissions outside the skill. Run the selected shadow cases manually.

Retrieve the exact authorized local JSON response of the existing
`GET /v1/agent-execution-traces` endpoint for the test window. Include
the root executions and their returned child runs; a response filtered to
one execution may omit children. Request a suitable existing event limit.
Retain the original response independently. Compute its canonical JSON
SHA-256 using the same digest function as the review utility, for example:

```sh
python -c "from shared.native_shadow_trace_review import load,digest; print(digest(load('trace.json')))"
```

Declare the package digest and one root execution for each case. Use the
exact observed framework and reviewed display labels from the existing
trace response; these labels are not native tool IDs or permission grants.

```json
{
  "package_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "cases": [
    {
      "case_id": "case_1",
      "root_execution_id": "execution:0000000000000001",
      "expected_framework": "claude_code",
      "reviewed_tool_labels": ["Read"]
    }
  ]
}
```

The example identifiers are placeholders. The case/package association is
a customer declaration, not independently established by the trace.

```sh
python -m shared.native_shadow_trace_review --trace trace.json \
  --trace-sha256 EXACT_TRACE_PIN --declaration declaration.json \
  --output shadow-trace-review.json --confirm-local-review
```

The review follows observed child links recursively, refusing contradictory
or cyclic membership and reuse of a run between cases. It reports missing
runs, missing run boundaries, truncation, missing framework/tool labels,
observed different frameworks, literal tool labels outside the reviewed
list, and observed failure signals. Unassigned returned runs and the
response's run-count limit remain explicit. It verifies excerpt and
operation-count consistency and pins the supplied trace and declaration.
The report references exact execution/event pairs without copying raw
event content or tool arguments. Opaque identifiers may still be sensitive.
Keep the original source for drilldown. Output cannot overwrite a file.

Statuses are deliberately not quality grades: observed deviation or failure,
incomplete observation, and no deviation **in returned events**. Even the
last status does not establish that all tools were observable, the client
restrictions worked, an exact staged skill was invoked, the model consumed
its instructions, or the underlying business outcome succeeded. Missing
events are not proof of absent actions. The report cannot establish
source authenticity, actual permission compliance, package identity,
case identity or current consent. Independently verify the deployed
package, test-case outputs, actual tool restrictions and source outcomes
using existing reviewed evaluation and source-check paths before any
separate rollout decision. No execution is authorized here.

## Validation

```sh
python -m pytest -q tests/test_native_shadow_trace_review.py tests/test_native_shadow_trace_integration.py
```

Core tests cover sixty combinations of returned tool labels, error status,
truncation, boundary and framework, plus child closure, missing runs,
pin/count validation and exclusive CLI output. The integration regression
uses an actual existing Claude hook event and structural trace route with
the auth boundary and retained event rows substituted; it is not an
installed-client test.
