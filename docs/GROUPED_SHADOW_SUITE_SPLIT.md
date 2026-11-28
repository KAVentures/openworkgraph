# Group-disjoint reviewed shadow suite preparation (R17/R25)

Before freezing a held-out planner evaluation, split a customer-reviewed
shadow suite into development and holdout sets without separating cases
that belong to the same declared business episode, customer record, or
other unit of potential dependence.

From the OWG repository, prepare the authorized reviewed-shadow-cases.v1
JSON suite (2–50 cases) and obtain its exact canonical JSON SHA-256 using
the same digest function as the generated runtime. Keep the original suite
private. Declare an opaque group for every case, for example:

```json
{"case_groups":{"case_1":"group_01","case_2":"group_01","case_3":"group_02"}}
```

Cases with shared episode or dependence must use the same group. The
group declaration is not inferred or source-verified. Declare the size
target and all category minima separately, for example:

```json
{"target_holdout_cases":10,"min_development":{"normal":2,"exception":1,"missing_facts":1},"min_holdout":{"normal":2,"exception":1,"missing_facts":1}}
```

Run:

```sh
python -m shared.reviewed_suite_split --suite reviewed-cases.json \
  --suite-sha256 EXACT_SUITE_PIN --groups groups.json --policy policy.json \
  --output-directory new-split --confirm-local-case-split
```

The planner refuses duplicate exact inputs, invalid case inventory and
incomplete group declarations. It enumerates reachable category count
vectors by dynamic programming across whole groups. Among nonempty splits
meeting both sets of category minima, it first minimizes absolute deviation
from the holdout size target, then total category proportional imbalance
(measured in integer cross-products), then lexicographic group-ID tuple.
It reports all feasible holdout sizes. No feasible split means no case files
are produced; the report explains infeasibility by the empty feasible-size
set. The procedure does not relax constraints or drop cases.

The output directory must not already exist. On success it contains private
development.json and holdout.json suites in the existing runtime format,
plus review.json with case IDs, counts and exact input/output pins but no raw
case values or group IDs. The two suite files contain original case data and
reviewed expectations and must be protected. Exit 0 means a split was found,
1 means infeasible, and 2 means invalid input or output failure. Discard any
partial output directory from a failed write; review.json is written last.

This utility validates the suite shape but does not validate graph routes
against the exported process specification. Run the existing runtime's
suite validation and the held-out freeze procedure (#1622) on both resulting
files before evaluation. No semantic overlap, provenance, true independence,
blinding or business outcomes are established by this procedure. In
particular, assigning different opaque groups to related cases can still
cause leakage; independent customer review of group assignment is essential.

The implementation is standalone and main-based, with no modification to
the runtime exporter or pending held-out acceptance branch.

```sh
python -m pytest -q tests/test_reviewed_suite_split.py
```

The tests compare against exhaustive subset enumeration in 500 randomized
small group populations, check disjointness and complete case retention,
infeasibility and invalid inputs, and exercise exclusive private output.
