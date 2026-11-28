# Suite-wide offline HTTP argument-field review (R17)

Customer-controlled generated runtime projects include
`suggest_http_fields.py`. Use it after reviewing a direct HTTP operation
catalog and selecting which existing non-human activity uses each operation,
before completing the argument mapping and authorizing execution.

Create a local selection file, using the actual activity IDs and catalog
operation names, for example:

```json
{"assignments":{"readback":"read_case","resolve":"resolve_case"}}
```

Run from the exported project:

```sh
python suggest_http_fields.py --cases reviewed-cases.json \
  --operations reviewed-operations.json --operations-sha256 EXACT_CATALOG_PIN \
  --selection selection.json --output field-review.json --confirm-local-field-review
```

The exact reviewed specification, catalog pin and authorized reviewed
case suite are validated using the existing runtime. The utility does
not infer activity-to-operation relationships, query an AI model or call
the provider. It does not create an enabled binding.

For each selected activity, it considers each distinct case whose reviewed
accepted routes contain that activity, including routes that repeat it.
For each required operation argument, it reports:

- Literal wire role (path, query, body, expected version or idempotency key).
- Named object paths that pass the existing adapter's individual scalar
  and role-specific checks in every relevant case.
- Number of full-support paths and best support count over relevant cases.
- Unique-compatible, ambiguous, no-full-support or no-reviewed-route status.
- A separate unreviewed argument-map hint for unique-compatible fields.

The projection permits named scalar leaves through at most six nested
object levels; arrays, nulls and unsafe names are omitted and counted.
Projection limits are 200 scalar leaves and 2,000 visited nodes per case;
compatibility checks are capped at two million. Exceeding the budgets
refuses the review rather than silently truncating the candidate set.
Output is capped at four MiB and cannot overwrite an existing report.
Case values are not copied into the report, but field names, operation
names, activity IDs and case coverage may be sensitive; use it locally.

A unique compatible field is not proof of correct business meaning.
Multiple arguments may have the same candidate. Combined request size,
cross-field relationships, actual service behavior, result predicates,
write controls, idempotency semantics, fresh read preconditions, source
checks and human work still require review. Clarification-only cases
and activities without selected operations remain visible. Review and
complete the bindings, then use the exact offline `rehearse_http.py`
case-suite checks before separately authorizing execution.

The independent core test compares case support against an oracle over
600 randomized suites. The generated runtime test uses the real HTTP
adapter predicates and verifies stale catalog refusal, negative version
and unsafe paths, no transport, and absence of raw values from the report.

This change is stacked on #1659, with #1632 and #1618 as prerequisites.
Preserve existing parent checks; integrate and validate the reconciled
current-main tree before merging this child.
