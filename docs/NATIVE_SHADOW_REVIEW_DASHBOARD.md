# Native shadow evidence review in the Agents dashboard (P0 #1663)

This is the UI and authenticated local API for the native shadow trace
review introduced by #1672. It reuses the existing structural agent trace
read and offline reviewer. There is no new event store, model invocation,
or business-system execution.

## Customer workflow

1. Configure the existing native agent adapter and independently restricted
   test environment. Verify the staged skill package separately; run the
   declared held-out test cases using the customer's existing AI client.
2. Open the **Agents** tab and expand **Review native shadow-test evidence**.
3. Enter the explicit test-window start and end as offset-aware ISO 8601
   timestamps (at most seven days). Choose whether to include disconnected
   integrations' retained history. Click **Capture exact trace snapshot**.
4. Save the original JSON if needed. The snapshot pins canonical bytes from
   the existing trace response; it remains in browser memory until a new
   capture, window change or page close. It is not automatically refreshed.
5. Enter the staged package digest. Select a returned root execution, enter
   its actual case ID, independently declare the expected framework and
   the reviewed exact tool display labels, one per line. An empty list
   declares that no tool calls are expected. Add up to 50 distinct cases.
   The observed framework label is shown separately; it is not substituted
   for the operator's expectation. Missing roots can be reviewed using the
   #1672 CLI with an explicitly declared opaque ID.
6. Click **Review declared shadow cases**. Inspect evidence gaps, literal
   tool-label deviations and failures with exact event references.
   Download the original trace, declaration and review JSON for reproduction.
   Changing the case population or package digest clears the review; changing
   the window or disconnected-history choice clears the entire snapshot.

The server enforces local dashboard-session authentication for both data
operations. The ordinary API/MCP bearer is insufficient. The underlying read
is limited to 25,000 retained source rows, 100 returned runs, and 500 events
per run. The source row bound is applied by the existing read before its
end-window and disconnected-history filtering; a returned count below the
limit therefore does not prove completeness. The UI displays visible
coverage limits and the reviewer preserves truncation and missing child
runs. Snapshots over 8 MiB are refused, not silently shortened. Request
bodies are bounded and duplicate JSON keys are refused.

The original snapshot JSON is kept as a string, so browser number
conversion cannot alter the artifact that is reviewed and downloaded.
Server-side review recomputes its canonical digest, then uses the existing
#1672 logic. No supplied report is trusted, but a content digest is not a
signature or proof that the supplied snapshot is authentic. The browser
does not persist evidence in local storage, and the feature creates no
server-side evidence or review records. Exports may contain sensitive
structural data and should be handled under the customer's retention policy.

Neither a clean review nor a matching digest proves tool permission
compliance, actual skill invocation, the model's use of the instructions,
case association, current source authorization or business outcomes.
Perform independent outcome verification and review prior to any separate
rollout authorization. The feature never grants permission to execute.

## Validation

```sh
python -m pytest -q tests/test_native_shadow_trace_review.py tests/test_native_shadow_review_routes.py
```

The HTTP regression uses a real FastAPI router and substitutes the retained
trace reader and dashboard-session validator. The existing Chromium
agent-run regression (tests/browser/event_log_agent_runs.cjs) now includes
the actual dashboard flow using its ten canonical seeded executions,
including snapshot capture, declaration, deviations and invalidation.
Full application/browser and installed-client acceptance remain required.
