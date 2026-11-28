# Independent-case critical failure qualification (R26)

Use this standalone offline tool before procuring an agent to determine
whether a planned independent case population could demonstrate a critical
failure rate below a declared limit. It complements the paired superiority
design (#1652); neither test replaces the existing procurement review,
independent outcome checks or rollout gates.

## 1. Design

Save a design JSON with one to twelve mutually exclusive strata:

```json
{"format":"openworkgraph.qualification-design.v1","alpha_bps":500,"strata":[
  {"stratum":"routine","limit_bps":100,"assumed_bps":20,"cases":400}
]}
```

```sh
python -m shared.agent_procurement_qualification design design.json
```

All probabilities are integer basis points (100 = 1%). For each stratum,
the report gives the largest failure count qualifying at the fixed
enrollment size, the minimum enrollment with zero failures (or null if
outside the 500-case-per-stratum planning cap), and the exact probability
of qualifying at the assumed true failure rate. The displayed probability
is rounded down; all decisions use integer arithmetic.

For example, one stratum with a 1% limit and 5% alpha requires 299 independent
cases with zero failures. Repeating one case 299 times is not equivalent.

## 2. Freeze the population

Choose opaque case hashes before reviewing any outcomes. Use a securely
generated opaque identifier or keyed hash, not a hash of a predictable
customer identifier. Define the endpoint, sampling frame, candidate
configuration and independence assumptions separately in the study protocol.

```json
{"format":"openworkgraph.qualification-plan.v1","alpha_bps":500,"strata":[
 {"stratum":"routine","limit_bps":100,
  "case_hashes":["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"]}
]}
```

The example is intentionally too small to qualify at that limit.

```sh
python -m shared.agent_procurement_qualification freeze plan.json > ledger.json
```

Retain the plan_sha256 independently of the ledger. It binds the exact
ordered plan content using canonical sorted-key compact JSON. This is a
content pin, not a signature or proof of prospective enrollment. The
template starts every enrolled case at unknown. Enter exactly one reviewed
outcome per case: failure, no_failure, or unknown. Do not remove cases.

## 3. Review

```sh
python -m shared.agent_procurement_qualification review plan.json ledger.json EXPECTED_PLAN_SHA256
```

The expected plan digest must match both the supplied plan and ledger pin.
Duplicate, missing, unplanned or altered-scope cases are refused. Per
stratum the result reports enrollment, failure, non-failure and unknown
counts, exact threshold and optimistic/pessimistic qualification. An
incomplete stratum cannot qualify even if all possible completions pass;
a stratum that cannot pass even under optimistic completion is not qualified.
All strata must qualify for the overall status to qualify.

## Mathematical contract

For n independent Bernoulli cases, null rate p0, family alpha a and m
prespecified strata, reject the null p >= p0 only when
P(Binomial(n,p0) <= observed failures) <= a/m. The binomial lower tail
decreases with p, so p0 is the least favorable null. Bonferroni controls
familywise error without requiring independence between the tests; each
individual test still requires its own independent case assumption.
Power is the same lower tail at the assumed alternative and the critical
failure count. The probability is per stratum, not joint probability.
No normal approximation, floating point threshold decision or optional
stopping is used.

## Limits

This is a user-declared, fixed-sample planning and review tool, not a
validated production safety certification. It does not authenticate
reviewers, candidate versions or source observations, establish prospective
chronology, handle correlated cases, account for repeated interim looks or
prove representativeness. It has a separate planning cap from the current
R26 paired trial. No automatic execution or approval; no network, raw
customer data, existing store mutation or production integration.

The CLI writes reports to stdout; choose a protected local destination and
avoid shell redirects to existing files. Run the focused tests with:

```sh
python -m pytest -q tests/test_agent_procurement_qualification.py
```
