"""One product-value report built on the existing continuity scorer.

This script NEVER calls a model or treats a model's prose as adjudication.
Each input arm must consist of completed runs with independent review. The
reported metrics are comparisons of submitted reviewed records, not proof that
the underlying runs took place; retain independent transport traces.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .continuity.scoring import _jsonl, aggregate, decision


ARMS = ("control", "raw", "goal")
REVIEW_FIELDS = (
    "selected_resources", "selected_agent_runs", "clarification_questions",
    "wrong_assumptions", "continuation_success", "consequential_wrong_action",
)


def _index(items: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    if not items:
        raise ValueError(f"{label}: no completed episodes to score")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{label}: rows must be objects")
        key = str(item.get("episode_id") or "").strip()
        if not key or key in indexed:
            raise ValueError(f"{label}: missing or duplicate episode_id {key!r}")
        indexed[key] = item
    return indexed


def _validated_review(run: dict[str, Any], *, episode_id: str, arm: str) -> dict[str, Any]:
    if run.get("recorded_model_completion") is not True:
        raise ValueError(f"{arm}/{episode_id}: only completed model turns can be graded")
    review = run.get("independent_review")
    if not isinstance(review, dict) or not str(review.get("reviewer_id") or "").strip():
        raise ValueError(f"{arm}/{episode_id}: explicit independent reviewer required")
    if not all(key in review for key in REVIEW_FIELDS):
        raise ValueError(f"{arm}/{episode_id}: incomplete independent review")
    if (not isinstance(review["selected_resources"], list)
            or not isinstance(review["selected_agent_runs"], list)):
        raise ValueError(f"{arm}/{episode_id}: selected evidence must be lists")
    if (not isinstance(review["continuation_success"], bool)
            or not isinstance(review["consequential_wrong_action"], bool)):
        raise ValueError(f"{arm}/{episode_id}: outcome flags must be reviewed booleans")
    for key in ("clarification_questions", "wrong_assumptions"):
        n = review[key]
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"{arm}/{episode_id}: {key} must be a non-negative integer")
    return {"episode_id": episode_id, **{key: review[key] for key in REVIEW_FIELDS}}


def report(
    episodes: list[dict[str, Any]],
    control: list[dict[str, Any]],
    raw: list[dict[str, Any]],
    goal: list[dict[str, Any]],
) -> dict[str, Any]:
    truth = _index(episodes, label="ground truth")
    arms = {
        arm: _index(rows, label=arm)
        for arm, rows in zip(ARMS, (control, raw, goal))
    }
    expected = set(truth)
    for arm, runs in arms.items():
        if set(runs) != expected:
            raise ValueError(
                f"{arm}: missing episodes {sorted(expected - set(runs))}; "
                f"extra episodes {sorted(set(runs) - expected)}"
            )

    reviewed: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
    for episode_id in truth:
        model_ids = set()
        source_states = set()
        for arm in ARMS:
            run = arms[arm][episode_id]
            model_id = str(run.get("model_id") or "").strip()
            state = str(run.get("source_state_id") or "").strip()
            if not model_id or not state:
                raise ValueError(f"{arm}/{episode_id}: model_id and source_state_id required")
            model_ids.add(model_id)
            source_states.add(state)
            reviewed[arm].append(_validated_review(run, episode_id=episode_id, arm=arm))
        if len(model_ids) != 1 or len(source_states) != 1:
            raise ValueError(f"{episode_id}: paired model/source state mismatch")

    metrics = {arm: aggregate(episodes, reviewed[arm]) for arm in ARMS}
    verified_real = all(
        truth[ep].get("real_observed_work") is True
        and str(truth[ep].get("ground_truth_verified_by") or "").strip()
        for ep in truth
    )
    # Neither the test fixtures nor the mere presence of reviewer-shaped JSON
    # should produce an unqualified product recommendation.
    ready = len(truth) >= 20 and verified_real
    comparisons = {
        "goal_vs_no_owg": decision(metrics["control"], metrics["goal"]),
        "raw_vs_no_owg": decision(metrics["control"], metrics["raw"]),
        "goal_vs_raw": decision(metrics["raw"], metrics["goal"]),
    }
    return {
        "schema": "owg.product-value-three-arm.v1",
        "status": "reviewed_real_episode_comparison" if ready else "pilot_only",
        "episodes": len(truth),
        "ground_truth_labeled_real": verified_real,
        "model_calls_performed_by_this_script": 0,
        "metrics": metrics,
        "comparisons": comparisons,
        "product_decision": (comparisons["goal_vs_no_owg"]["decision"]
                             if ready else "insufficient_reviewed_real_episodes"),
        "limitations": [
            "This scorer validates records but cannot independently verify their provenance",
            "Store model transport traces and actual source-system outcomes separately",
            "A successful retrieval or a passed scoring threshold alone is not causation",
            "Use fresh, paired sessions and blinded review before reporting effectiveness",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare reviewed OWG product-value arms")
    parser.add_argument("--episodes", type=Path, required=True)
    for arm in ARMS:
        parser.add_argument("--" + arm, type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = report(
        _jsonl(str(args.episodes)), _jsonl(str(args.control)),
        _jsonl(str(args.raw)), _jsonl(str(args.goal)),
    )
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
