# Typed case-metric minimum / maximum (roadmap #370 M5/R9)

The existing nonrecursive, version-1 typed arithmetic expression format now
accepts `"operation": "minimum"` and `"operation": "maximum"` in addition to
add, subtract, multiply and divide.

Both operators require matching exact declared units. A declared `EUR/hour`
label remains distinct from an arithmetic compound unit; no currency conversion
or unit inference happens. Invalid units, cyclic references, non-finite typed
constants and unknown operands continue to fail at preflight without reading
event rows or executing expression text.

DuckDB's built-in LEAST/GREATEST ordinarily skip NULL operands. These
expressions deliberately **do not**: a case with an absent operand stays
missing and participates in existing explicit coverage counts instead of being
silently filled with the other operand.

Example extension to a case analysis:
```json
{
  "name": "capped_eur",
  "kind": "arithmetic",
  "expression_version": "1",
  "operation": "minimum",
  "left": {"kind": "metric", "name": "spend"},
  "right": {"kind": "constant", "value": 8.0, "unit": "EUR"}
}
```

This is a bounded additive operator slice, not generic expression syntax,
source data enrichment or broad case-language parity. It introduces no
source I/O, default synchronization, new authorization or agent behavior.
