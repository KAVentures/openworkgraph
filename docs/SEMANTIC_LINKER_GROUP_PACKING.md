# Coverage-aware semantic group packing (Worker C, #326)

When an opted-in semantic model proposes pairwise links, OWG now optimizes the disjoint grouping of each connected component containing at most ten candidate items. It enumerates only fully pair-supported, business-record-compatible groups and selects the partition with the most covered items, then the most supported within-group pairs, then a deterministic lexical tie-break. This avoids consuming three items in one large group when two safe groups could cover four.

For components above ten items, the existing bounded maximum-clique/greedy fallback remains. No time-only edges are added; conflicting same-kind business records cannot share a group. This changes only **review-required semantic suggestions**, not canonical evidence, consent, endpoint settings, record identity, REST/MCP schema, or automatic agent authorization.

Run `pytest -q tests/test_semantic_linker_group_packing.py tests/test_semantic_linker_exact_clique.py tests/test_semantic_linker_max_coverage.py`. Synthetic correctness tests do not prove the preregistered F1 ≥ 0.90 / contamination ≤ 0.05 thresholds; independent development-family and frozen-holdout evaluation and consenting-user pilot are still required.
