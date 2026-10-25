# Arithmetic case metrics

The local analysis builder supports a bounded expression vocabulary (`expression_version: "1"`).
This extends R9 in issue #370; it is not general SQL/PQL, a transformation engine, a business
attribute join or an execution policy. No provider reads or new permissions are involved.

Use the existing `POST /v1/event-logs/{id}/analysis` human-dashboard endpoint. For example:

```json
{
  "level": "cases",
  "metrics": [
    {"name":"spend","kind":"event_attribute_sum","attribute":"amount","unit":"EUR"},
    {"name":"items","kind":"activity_count","activity":"Ship"},
    {"name":"per_item","kind":"arithmetic","expression_version":"1","operation":"divide",
     "left":{"kind":"metric","name":"spend"},"right":{"kind":"metric","name":"items"}}
  ],
  "measures":[{"fn":"avg","of":"metric:per_item"}]
}
```

This is the **average of available per-case ratios**, not total spend divided by total shipments.
The source values and chosen case notion determine its meaning; counting an event does not
establish a physical shipment, business cost or benefit. Object-centric flattening can duplicate
shared events across cases. Inspect those diagnostics before aggregating business value.

Operands are either an earlier named metric or `{"kind":"constant","value":2,"unit":"ratio"}`.
Constants must be JSON finite numbers (booleans and numeric strings are refused). Their unit defaults
to `ratio`, the reserved dimensionless label. Four metrics total, two operands each, no nested
expressions, no forward/self/cyclic references, no user query text, functions, exponent operator
or executable source. A dependency may itself be an earlier arithmetic metric. The planner performs
one vector operation per selected case per arithmetic metric; it retains the existing workspace
query limits and lock. This does not establish concurrent enterprise-scale acceptance.

`add`, `subtract`, `multiply` and `divide` use finite DOUBLE arithmetic. A missing operand yields
missing. Zero divisors, including negative zero, yield missing. Nonfinite results/overflow are
withheld and propagate as missing to dependent metrics. Missing values are excluded from aggregates;
all-missing groups return null. Coverage is counted after the same cohort/case filters used for
results: `cases_with_value`, `cases_without_value`, `cases_with_missing_operand`,
`cases_with_zero_divisor` and, when present, `cases_with_non_finite_value`. Zero-divisor counts
require an available numerator; the categories do not substitute for inspecting the source metrics.
No division-by-zero infinity or NaN enters JSON.

Units are **exact declared atomic labels**. There is no exchange rate or time conversion, label
normalization or parsing of labels such as `EUR/hour`. Add/subtract require identical symbolic
unit powers. Multiply adds powers, divide subtracts them, and cancelled powers disappear.
Equal units divided give `ratio`. Output `unit_powers` preserves atomic labels unambiguously;
`unit` renders them, for example `(EUR) * (events)^-1`. Original metric units remain unchanged.
Consequently `EUR`, `eur`, `USD`, ` EUR` and `EUR/hour` differ. Declaring an inaccurate unit remains
a user error; matching labels do not independently verify the data. Floating point results and
aggregates use the existing rounding/overflow semantics, not financial decimal arithmetic.

In **Build an analysis → Case metrics**, add source metrics first, choose Arithmetic, select an
operation and either an earlier metric or a typed constant on each side. Then choose the new metric
under Measure. Dependencies cannot be removed until their dependents are removed. The server
validates the entire definition, including units, on Run and Save analysis.

The existing per-log metric library can retain an arithmetic **symbolic template**. It does not copy,
pin or automatically load dependencies; matching names can deliberately refer to different definitions
in another analysis. Add dependencies first and inspect their definitions. **Save analysis** instead
retains the entire ordered definition list independently of the metric library. Rerunning a saved
analysis uses the current dataset with those definitions, not a historical dataset snapshot.

JSON export contains the full spec, units, coverage and numeric results. CSV remains the existing
numeric table with named measure columns; use the JSON alongside it for definition/unit/coverage
provenance. Neither export changes the source or authorizes automation. Delete the log to delete
its saved analyses and metric templates under the existing lifecycle.

Acceptance includes hand-calculated arithmetic/coverage, invalid/injection/dependency/unit refusal
before dataset access, unchanged saved analyses after template deletion, real HTTP authentication,
Chromium form/save/template/export/zero/error flows and axe. The opt-in
`OWG_SCALE_ACCEPTANCE=1 pytest -q -s tests/test_event_log_arithmetic_metrics.py` populates owned
synthetic data directly in the imported-log schema: 500,000 cases / 999,999 events, all four metric
slots, independently counted zero divisors and a 120-second analysis budget. This tests analysis,
not CSV import or concurrent users. In the development Linux/Python 3.12/DuckDB 1.5.6 environment,
the measured query took 2.054 seconds; that is not a production latency guarantee.
