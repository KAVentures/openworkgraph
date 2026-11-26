"""Optional all-pairs review of an already authorized immutable portfolio.

Compatibility is pair-specific. No pooling, ranking, inferred identity, or
transitive eligibility: every pair must pass the existing comparison engine.
"""
from collections import Counter
from itertools import combinations

from .analysis_revision_comparisons import compare_snapshots


def pairwise_review(details, contradictions):
    pairs = []
    reasons = Counter()
    for i, j in combinations(range(len(details)), 2):
        pair = compare_snapshots(details[i], details[j])
        refusal = list(pair["refusal_reasons"])
        if i in contradictions or j in contradictions:
            refusal.append("portfolio_conflicting_claimed_basis")
        comparable = not refusal
        reasons.update(refusal)
        pairs.append({"left_index": i, "right_index": j, "comparable": comparable,
                      "refusal_reasons": refusal,
                      "changes": pair["changes"] if comparable else [],
                      "same_claimed_source": pair["same_claimed_source"],
                      "same_claimed_analysis_spec": pair["same_claimed_analysis_spec"]})
    counts = [{"selection_index": i,
               "comparable_neighbors": sum(p["comparable"] for p in pairs
                                           if i in (p["left_index"], p["right_index"])),
               "withheld_neighbors": sum(not p["comparable"] for p in pairs
                                         if i in (p["left_index"], p["right_index"]))}
              for i in range(len(details))]
    # Give analysts direct compatibility cohorts only where every selected
    # member pair passes. Maximal cliques may overlap; never treat them as
    # disjoint source populations. At most 8 selections => 255 subsets.
    edges = {(p["left_index"], p["right_index"]) for p in pairs if p["comparable"]}
    groups = []
    for size in range(len(details), 1, -1):
        for indices in combinations(range(len(details)), size):
            if any(set(indices).issubset(group) for group in groups):
                continue
            if all(edge in edges for edge in combinations(indices, 2)):
                groups.append(list(indices))
    return {"format": "openworkgraph.shared-analysis-pairwise-review.v1",
            "pair_count": len(pairs),
            "comparable_pair_count": sum(p["comparable"] for p in pairs),
            "withheld_pair_count": sum(not p["comparable"] for p in pairs),
            "pairs": pairs, "selection_coverage": counts,
            "refusal_reason_counts": dict(sorted(reasons.items())),
            "maximal_pairwise_compatible_sets": groups,
            "isolated_selection_indices": [x["selection_index"] for x in counts
                                           if not x["comparable_neighbors"]],
            "sets_can_overlap": True,
            "sets_are_disjoint_populations": False,
            "transitive_comparability_assumed": False,
            "pooled_totals_reported": False, "ranking_reported": False,
            "interpretation": "Every unordered pair was checked separately. Deltas run from the lower selection index to the higher one. Compatible sets contain only directly compatible pairs; they may overlap and do not establish independent or disjoint populations. No pooling, ranking, source verification or execution authority."}
