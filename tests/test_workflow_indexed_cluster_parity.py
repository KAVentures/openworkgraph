"""Fast workflow grouping must preserve exact complete-link cluster choices."""
from __future__ import annotations

import random

from server import workflow_candidates as module


def _run(i, steps, actor="human"):
    return {
        "execution_id": f"execution:{i:016x}", "actor_kind": actor,
        "structural_steps": list(steps), "semantic_steps": list(steps),
        "started_at": f"2026-10-14T08:00:00Z",
        "duration_seconds": 420,
    }


def _reference_memberships(runs, threshold=.72):
    exact = {}
    for run in runs:
        sequence = module._sequence(run["structural_steps"])
        if sequence:
            exact.setdefault((run["actor_kind"], sequence), []).append(run)
    variants = sorted(exact.items(),
                      key=lambda x: (-len(x[1]), x[0][0], x[0][1]))
    groups = []
    for variant in variants:
        (actor, sequence), _members = variant
        best = None
        best_score = -1.0
        for i, group in enumerate(groups):
            if any(k[0] != actor for k, _runs in group):
                continue
            scores = [module.sequence_similarity(sequence, k[1]) for k, _runs in group]
            if not scores or min(scores) < threshold:
                continue
            score = sum(scores) / len(scores)
            if score > best_score:
                best_score, best = score, i
        if best is None:
            groups.append([variant])
        else:
            groups[best].append(variant)
    return sorted(
        tuple(sorted(r["execution_id"] for _key, members in group for r in members))
        for group in groups
    )


def test_indexed_grouping_matches_reference_complete_link_membership():
    rng = random.Random(20261014)
    vocabulary = tuple(f"surface:app{i}" for i in range(13)) + (
        "action:copy", "action:paste", "action:save", "action:open",
    )
    runs = [
        _run(i, rng.choices(vocabulary, k=rng.randint(2, 7)),
             actor="agent" if i % 5 == 0 else "human")
        for i in range(70)
    ]
    expected = _reference_memberships(runs)
    actual = module.cluster_runs(runs, min_runs=1, limit=100)
    received = sorted(tuple(sorted(row.get("execution_ids") or [])) for row in actual)
    assert received == expected


def test_completely_disjoint_variants_skip_all_pairwise_scores(monkeypatch):
    calls = 0
    original = module.sequence_similarity

    def monitored(a, b):
        nonlocal calls
        calls += 1
        return original(a, b)

    monkeypatch.setattr(module, "sequence_similarity", monitored)
    rows = [
        _run(i, [f"surface:unique{i}", f"action:unique{i}"])
        for i in range(500)
    ]
    result = module.cluster_runs(rows, min_runs=1, limit=100)
    assert len(result) == 100
    assert calls == 0  # 500 disjoint variants; never compare impossible pairs
