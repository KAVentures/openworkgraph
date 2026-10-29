# Typed pairwise case metrics: average and absolute difference (R9/M5, #370)

The existing version-1, nonrecursive arithmetic metric accepts two additional
fixed operators: `"average"` and `"absolute_difference"`. Each requires
**exactly matching unit powers** on both operands. No unit conversion, parsing
of opaque labels (such as `EUR/hour`), source enrichment, arbitrary SQL,
expression nesting or new authorization is introduced.

- `average` computes the per-case arithmetic mean of the two operands.
  For operands with the same sign, it evaluates `a + (b - a) / 2.0` to
  avoid overflow while retaining equal subnormal values; for opposite signs
  it halves each operand before adding, avoiding intermediate overflow.
- `absolute_difference` computes `abs(a - b)` per case. A difference that
  overflows to a non-finite result is **missing**, not a fabricated finite
  value, under the existing non-finite coverage contract.
- Both operators preserve NULL: if either operand is absent, the case result
  is absent and `cases_with_missing_operand` increments. They do not silently
  substitute zero or the present operand.
- Invalid units, missing or forward references, cyclic/self references,
  unsupported operations and non-finite constants fail during typed
  preflight, before dataset access. Existing four-metric and query limits
  remain unchanged.

Example after a declared `spend` metric in EUR:

```json
{
  "name": "mean_spend",
  "kind": "arithmetic",
  "expression_version": "1",
  "operation": "average",
  "left": {"kind": "metric", "name": "spend"},
  "right": {"kind": "constant", "value": 8.0, "unit": "EUR"}
}
```

This is a bounded local case-analysis increment, not a general expression
language, source permission, agent action, live customer acceptance or a
claim of full R9 completion. See `CASE_METRIC_EXTREMA.md` for minimum and
maximum, which also propagate missing operands.
