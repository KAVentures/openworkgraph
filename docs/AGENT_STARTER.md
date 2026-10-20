# Safe agent starter from a reviewed process proposal

This is a first step toward turning mapped processes into agent implementations
without building another entire RPA runtime inside OpenWorkGraph.

## Flow

1. Analyze an explicitly selected CSV event log locally.
2. Inspect a complete observed process variant.
3. Open the optional AS-IS/TO-BE designer and edit a proposed target process.
4. Enter the desired business outcome and confirm review.
5. Choose **Download agent starter ZIP** from the reviewed proposal.

The exporter is attached to OWG's existing locally authenticated API. It does
not save the proposal or send it to an AI service. An agent starter is a
*development scaffold*, not a deployed automation.

## ZIP contents

- `workflow.spec.json`: opaque, structured steps with no live tool bindings.
- `process.bpmn`: non-executable BPMN 2.0 sequential process model without decision branches or layout.
- `AGENTS.md`: fixed implementation safeguards; observed goals and steps are
  NEVER injected as agent instruction text.
- `README.md`: how to map source system, business policy, approvals and tools.
- `dry_run.py`: prints the step sequence without performing work.
- `tests/test_contract.py`: confirms tool bindings and permissions remain off.

The static AGENTS.md/README.md are deliberately separated from the possibly
adversarial or sensitive goal text inside the JSON data file.

## Security contract

- Export requires an explicit human-reviewed OWG proposal with all permission,
  outcome and source-ID flags set to the safe non-executable values.
- 32 KiB total HTTP request body; true streamed bound, including chunked upload.
- Max 40 steps; structural 64-character allowlist; reviewed goal max 500 chars.
- No credentials, native customer IDs, raw source events or arbitrary extra
  request fields copied to the output.
- No provider calls, background sync, AI execution, file persistence,
  "auto approve" or on-device agent installation.
- Source/tool mappings, policy, decision rules, exception branches, identity,
  approvals and independent business success receipts are explicitly unknown.
- Generated ZIP uses fixed filenames and no user-controlled archive paths.

A later, separately authorized agent-runtime integration can map this reviewed
plan to concrete provider tools, add sandbox tests, and evaluate real outcomes.
It must not infer write permission from previously observed employee clicks.

## Release gates

Unit tests cover malicious user text remaining outside AGENTS.md, disabled
execution, deterministic ZIP, safe step labels, and authenticated bounded API
responses. GitHub CI and a real local-browser download smoke test are required
before merging the integration PR.

Integration: from a reviewed AS-IS/TO-BE proposal, select **Download agent starter ZIP**. This sends only the minimized reviewed draft to the already authenticated local OWG process.

The BPMN file is only a descriptive linear model, not an executable RPA workflow. Its source labels are user-reviewed structural data; decisions, gateways, connector mappings and production authorization must be designed separately.

Integration note: the downloadable starter is based on the current merged OWG process-mining and opportunity-review stack. The generated runner still has no live actions.
