# P0 AI-native implementation integration candidate

This candidate combines exact parent histories from #1695 (implementation
journey), #1681 (isolated Claude skill setup), #1682 (frozen shadow roster)
and #1683 (joint native/source cohort). All four were based on main
375b34affa138d13685c816e55836aea3cc25f20 at assembly time. Three roster
overlaps were resolved by retaining the exact newer roster versions, after
checking the joint versions against their shared #1680 ancestor. The three
portfolio/source-cohort overlaps combine the existing implementation
dashboard and the source cohort mount, router and browser regression;
parent patches were applied with unique exact context. Browser parent
summary selectors are scoped to their direct child.

The independent #1666 alternative external proposal contract is deliberately
not incorporated here: it overlaps semantically with the v1 contract on
which this chain depends. It remains open and untouched; reconcile contract
coverage and any desired compatibility before retiring either approach.

## Acceptance

Run the repository's full integration and packaging checks on the combined
commit. The new cross-branch test uses the real proposal reviewer, renderer,
staged manifest and isolated installation, then freezes a test roster and
reconciles one assigned and one unassigned synthetic trace. It does not
invoke an actual model, establish business outcomes or authorize deployment.
Run it with:

```sh
python -m pytest -q tests/test_ai_native_integrated_contract.py
```

Also exercise the actual dashboard observation -> reviewed proposal ->
planning -> native skill -> roster -> retained source review flow, including
the existing browser regression suites. Validate real model and provider
acceptance, persisted evidence, quality, cost and authorization boundaries
before production claims. Individual focused tests on earlier branches do
not establish full combined-tree correctness. No parent is merged, closed,
retired or retargeted by this integration candidate.
