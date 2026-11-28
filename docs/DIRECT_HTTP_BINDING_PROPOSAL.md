# From HTTP field review to an editable binding proposal (R17)

The exported customer runtime now includes `propose_http_bindings.py`.
After importing and reviewing a direct HTTP operation catalog, run
`suggest_http_fields.py` with the exact reviewed suite and selection.
Choose a full-support field path for every argument of each activity
you want to include. For example, save this choices document:

```json
{"argument_choices":{"readback":{"issue":"/issue"},"resolve":{"issue":"/issue","expected_version":"/version","request_id":"/request_id"}}}
```

Use the review SHA-256 printed by the field review, not a digest of the
pretty-printed JSON file:

```sh
python propose_http_bindings.py --cases reviewed-cases.json \
  --operations reviewed-operations.json --operations-sha256 EXACT_CATALOG_PIN \
  --selection selection.json --field-review field-review.json \
  --field-review-sha256 EXACT_REVIEW_PIN --choices choices.json \
  --output bindings-draft.json --confirm-disabled-proposal
```

The command recomputes the original review from the exact inputs and
checks the entire report and supplied pin. A changed suite, specification,
catalog, activity selection or report requires a fresh review. Every
included activity needs explicit choices for all required arguments
(including fields with a unique candidate); each path must have full
support. A subset of reviewed activities can be included, with omitted
non-human activities listed as unmapped. No-route activities cannot be
included, even for an operation with zero arguments.

For every included activity, the actual HTTP adapter constructs the
complete request for every distinct relevant reviewed case. This checks
combined argument constraints and request byte bounds, not only individual
field compatibility. A failure refuses the entire draft. No network
transport is created and neither requests nor case values are exported.

The result uses the existing customer binding document format. The global
enabled flag is false, every operation kind is unmapped, every authorization
flag is false, and result predicates, concurrency controls, idempotency
contracts, read preconditions and source record mappings remain incomplete.
The local source endpoint placeholder is the existing runtime default
(127.0.0.1:8787); review it before enabling anything. The proposal metadata
records exact input pins, checks and required follow-up. It is not a
credential, approval, execution intent or live provider acceptance.

Edit a separate copy, review provider and business semantics, complete the
required contracts and source mappings, and use `rehearse_http.py` before
any separate authorization. The tool creates a new file only. It never
updates an existing mapping or grants execution. Exit 2 means the input,
request validation or output was refused.

The standalone core tests cover explicit choices, case-population accounting,
whole-request rejection, no value leakage and disabled output. The actual
generated-runtime suite exercises the same field review and HTTP adapter
with synthetic cases and stale-report refusal.

This feature is stacked on #1660 (prerequisites #1659, #1632 and #1618).
Integrate and validate the reconciled tree before merging; do not merge
into the parent branch or restart its checks.
