# Portable customer runtime synthetic acceptance (R17)

Generated Agents SDK ZIPs include **check_runtime.py**, in addition to the
read-only release verifier. It enables CI or a customer operator to collect a
machine-readable synthetic acceptance report for one exact export. No model
API or actual business-case execution is needed.

## Operator procedure

1. Obtain a trusted exported ZIP and record its manifest SHA-256 independently
   in the release record, using a trusted verifier.
2. Extract into a dedicated directory. Create an isolated Python environment
   and install the generated requirements. Do not make provider credentials,
   customer data or production connectivity available.
3. Verify the pinned generated files before executing code. Then run:

       python check_runtime.py --manifest-sha256 PINNED_HEX_DIGEST --confirm-trusted-synthetic-tests

4. Preserve the JSON report separately with the independently pinned digest.

Exit codes: 0 = all required suites executed and passed, 1 = failed (including
runner error or timeout), 2 = refused verification/confirmation, 3 = incomplete
(skipped or expected-failure tests, or empty suites).

The checker verifies the manifest and stages only inventoried files in a private
temporary directory, then verifies the copied files again. The test processes
receive an allowlisted environment and a temporary home. Individual suite
processes have 120-second timeouts. Output contains workflow reference, manifest
digest, suite names and counts only, not test logs or exception text. Real
customer bindings, credentials, case files and journals added to the original
directory are not copied. Decision-table exports include their dedicated suite.

The synthetic tests exercise structural routes, SDK offline model behavior,
execution safeguards, monitoring, configuration, reviewed shadow comparisons,
and source planning/evaluation. The SDK can be missing or fail to import; a
skipped required suite or individual skipped test is not reported as passing.

## Limits

This is a trusted-code test harness, **not** an OS sandbox, an authenticity
signature, a remote runtime attestation, an independent business-case test
or authority to execute. Use an isolated environment with network access
disabled by the operator. The manifest is unsigned; an independently recorded
digest and trusted code are essential. The harness does not install
dependencies, run a real planner, or use actual provider tools. It neither
changes the approved workflow nor updates OWG release declarations.

Next acceptance step: evaluate real authorized held-out normal/exception cases
using the existing reviewed shadow case tools, then validate actual tool
mappings and independent provider readback under explicit customer approval.

Repository tests: `pytest tests/test_customer_runtime_acceptance.py tests/test_process_agent_release_integrity.py tests/test_process_agent_project.py`.

Roadmap: [#370 R17](https://github.com/KAVentures/openworkgraph/issues/370).
