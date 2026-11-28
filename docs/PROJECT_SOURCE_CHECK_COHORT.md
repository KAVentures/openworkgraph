# Reconcile source-state checks for an entire declared test cohort (P0 #1663)

This feature reuses the existing opportunity project, confirmed source-state
expectations, consented source-check exports and retained exact-byte review.
It does not introduce another provider adapter, outcome store or agent
runtime.

## Customer workflow

1. Open a process-linked opportunity and its exported project in the local
   automation pipeline. Use the existing consented source-check export
   workflow to obtain any authorized provider receipts. Preserve the original
   files unchanged. This cohort review does not itself call a provider.
2. Within the selected source-state check panel, expand **Reconcile source
   checks for a declared test cohort** and load the confirmed expectations
   for the selected project.
3. Declare one confirmed terminal operation per case, up to 25 cases. Enter
   a unique opaque case ID, select the expectation, and enter the exact
   observed record reference when known. Select the corresponding original
   JSON download if available. A missing file is an explicit missing case,
   not a reason to exclude it. Include the entire intended cohort.
4. Reconcile the declared cohort. Each supplied file is checked by the
   existing local retained-export reviewer, before its declared terminal
   activity, operation and record reference are compared to the original.
   Reused documents, reused record/operation pairs, unrelated exports,
   altered bytes and changed idea/expectation basis are refused.
5. Review the counts and per-case findings. Download the minimized review
   and, separately, the private declaration needed to reproduce the mapping.
   The declaration includes observed record references; protect it and the
   originals under customer retention policy.

Each case is counted as a historical operation reported verified,
historical operation not established, or missing document. An additional
flag identifies historical references that need local review, such as a
changed idea revision, superseded process or changed/unavailable retained
run association. A historical provider operation is not a verified business
outcome, and an unavailable check is not proof of failure. The denominator
is the declared population only; completeness, independence, case identity
and agent causality are not established. Repeated checks of one record and
operation across cases are refused rather than silently double-counted.
This first slice supports one expected operation per case, not an arbitrary
multi-operation outcome model.

The server requires a local dashboard session, limits the JSON envelope
to 4 MiB and each original document to 64 KiB, and uses the existing
project checks without any fresh source read. Receipt bodies and private
record references are not copied into the report or stored by the new
feature. The report pins the declared case population, original document
hashes, exact project specification and reviewed idea revision. The
private declaration is intentionally separate; neither download establishes
a signed attestation or an atomic snapshot across local stores. No state
transition, execution permission, deployment, or business outcome is
authorized by a passing review.

## Validation

```sh
python -m pytest -q tests/test_project_source_cohort.py tests/test_project_source_cohort_integration.py
```

The focused pure tests exhaust all 64 combinations of three-case verified,
missing, unknown and stale states, with duplicate, exact-byte and changed
basis refusal. The integration regression uses the existing retained
source-check fixture and actual local review with substituted dashboard
session verification. The existing Chromium automation pipeline suite
exercises the actual unknown source receipt, missing case, report download
and stale-report invalidation. Full repository and browser acceptance are
required before release.
