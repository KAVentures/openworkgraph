# Frozen held-out planner acceptance (R17)

Advance #326 and #370 R17: determine whether a generated workflow planner
matches independently reviewed customer expectations on a frozen selected
evaluation cohort. This is separate from the existing synthetic runtime
suite, binding rehearsal, shadow comparisons and execution authorization.

## Three-stage workflow

Use a trusted generated Agents SDK project whose manifest was verified
against an independently recorded digest. Prepare two separately selected,
authorized and reviewed shadow suites using the existing
openworkgraph.reviewed-shadow-cases.v1 format. Development cases may be
used to improve the planner. Holdout cases should be selected and their
expectations adjudicated before any candidate capture. Do not select or
revise the holdout using candidate results.

### 1. Freeze

Copy heldout-policy.example.json to a new policy file and review all
thresholds. Every normal, exception and missing_facts category must have
an explicit positive minimum case count and an explicit minimum match
rate in integer basis points. One hundred percent is 10000. Also declare
the minimum overall match rate, maximum critical failures, maximum
regressions and whether a baseline capture is required. The supplied
example is deliberately strict; it is not a statistical recommendation.

    python heldout.py freeze --development development.json --holdout holdout.json --policy heldout-policy.json --output trial --confirm-retain-sensitive-challenge

The two suites must bind the same exact specification, have unique exact
case-input digests within each cohort and have no exact input overlap
between cohorts. Category minimums must already be met. Select at most 50
cases in either suite. The command creates a new local directory with:

- private-freeze.json: private aliases, suite digests, policy and challenge pin.
- challenge.json: exact selected case inputs and random opaque case aliases,
  exact specification reference and challenge identity. No original case
  IDs, declared categories, reviewed statuses, accepted routes or policy.

Record both printed digests in an independently controlled review record
before capture. Keep both original suites and the private freeze with the
evaluator. Do not share the private file or answer keys with the planner.
Inspect input values themselves for embedded answers, sensitive material
or unauthorized information before transferring the challenge. Exact
digest disjointness does not rule out semantic duplicates, prior exposure
or leakage through the selected input.

### 2. Capture

An authorized planner operator receives the challenge file, its independently
recorded digest and the same reviewed runtime specification. The capture
command intentionally does not take development, holdout or freeze files.

    python heldout.py capture --challenge challenge.json --challenge-sha256 CHALLENGE_DIGEST --model MODEL_ID --output candidate.json --confirm-send-case-inputs-to-model --confirm-retain-model-output

The existing no-business-tools planner is used. Each selected input and
the reviewed process specification are sent to the selected provider.
The provider does not receive the private expected routes through this
command. The two confirmations separately authorize disclosure and local
retention of potentially sensitive model output. Provider failure refuses
an incomplete capture rather than saving a partial population. No business
tool, business write or operational outcome verification is performed.

Capture a baseline using the same challenge when required. An external
authorized planner may also produce the documented capture format:
openworkgraph.heldout-capture.v1, challenge_sha256 and results, with
exactly one challenge_case_id and plan per row. Plans use the existing
shadow result plan schema. Record capture digests independently before
unblinding. Identical captures are reported explicitly and do not prove
that two independent trials occurred. The tool does not verify model
identity, capture independence or chronological order of external records.

### 3. Score

Back in the evaluator's trusted environment:

    python heldout.py score --development development.json --holdout holdout.json --freeze trial/private-freeze.json --challenge trial/challenge.json --freeze-sha256 FREEZE_DIGEST --candidate candidate.json --candidate-sha256 CANDIDATE_DIGEST --baseline baseline.json --baseline-sha256 BASELINE_DIGEST --output acceptance.json --confirm-reviewed-evaluation

Reconstruct the public challenge from the original frozen suite, verify
all exact pins, validate complete capture population and evaluate each
captured plan using the existing shadow comparator. Bad individual plans
count as failures rather than being dropped. Reuse paired comparison
for case-level regressions. Use exact integer comparisons for rate gates,
not rounded displayed percentages. A proposal on an expected-clarification
case is critical even if it is also malformed; explicit unresolved review
bypasses are critical too. If a baseline is optional and absent, regression
status is null rather than a claimed zero-regression result.

The minimized deterministic report includes exact pins, per-category
counts/rates, critical and regression gates, paired transitions, opaque
case outcome/reason codes and its own canonical digest. It does not contain
case values, planner text or accepted paths. Exit 0 means the selected
frozen gates passed, 3 means at least one failed, and 2 means the input
or approval was refused. Passing is not a release approval, model
certification or authorization for execution.

## Retention and limits

JSON artifacts are bounded to 1 MiB (reviewed suites/specification to
256 KiB; policy to 32 KiB). Duplicate JSON keys and nonstandard numbers
are refused. New files are exclusively created, with POSIX mode 0600
and new freeze directories mode 0700; Windows inherits ACLs. Existing
artifacts are not overwritten. Use a protected local directory and
customer retention rules. The challenge and capture are sensitive.
Hashes are consistency pins, not signatures or trusted timestamps;
independent custody of pins and review materials is essential.

The freeze/score path is offline. The capture path may contact the
operator-selected model provider only with explicit approvals. None of
these stages proves actual blinding, population representativeness,
independent business ground truth, provider operation or business outcomes.
A real pilot with independently adjudicated outcomes and appropriate
customer-controlled live acceptance is still required.

## Validation

The exact extracted evaluate_frozen function (4,144 characters; FNV-1a
2022960035) was reproduced locally and checked on 5,000 randomized
simulated comparator result populations (133,705 case outcomes). Exact
fraction arithmetic independently checked category and overall thresholds;
independent counts checked critical failures and regressions; report
digests were checked. Existing comparator and pin-validation dependencies
were stubbed in this isolated test, so this is not end-to-end validation.

The exported runtime includes test_heldout.py with the existing actual
synthetic shadow fixture, capture isolation, cohort/pin/refusal, policy,
malformed-plan, private-artifact and CLI tests. The generated project
test and portable synthetic acceptance checker exercise the new suite.
A focused draft CI workflow runs generated-project, release-integrity
and runtime-acceptance tests. Full local checkout is blocked by DNS;
the generated-runtime tests have not executed locally. CI and actual
customer holdout acceptance remain outstanding.
