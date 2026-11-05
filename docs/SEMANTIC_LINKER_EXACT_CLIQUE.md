# Bounded exact semantic subgroup selection

The optional, explicitly configured semantic linker continues to return review-only
suggestions. For up to 14 remaining candidate items it now enumerates compatible
pair-supported cliques and selects the largest, breaking ties by sorted item IDs.
This avoids seed-order and degree-order traps in the earlier greedy algorithm.

The search stops after 12,000 explored nodes and falls back to the existing
deterministic greedy selection; groups larger than 14 also use that fallback.
No additional model calls, evidence collection, automatic canonical merges,
permissions or sharing are introduced. Each selected member must have a direct
model-supported pair with every other member, and conflicting business records
of the same provider/kind cannot be joined.

The focused oracle regression compares small random interleaved candidate graphs
with exhaustive maximum-clique enumeration, and checks business-record vetoes.
This does **not** demonstrate a gain in reconstruction F1 or contamination:
the #326 family holdout and a consenting-user pilot remain independent gates.
