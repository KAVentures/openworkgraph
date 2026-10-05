from __future__ import annotations

"""Deterministic scoring for the OpenWorkGraph continuity A/B.

The runner intentionally does not call a model. Collect predictions from the
same frontier agent under two context conditions, then score both files here.
"""

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_episode(episode: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    correct = _set(episode.get("correct_resources"))
    selected = _set(prediction.get("selected_resources"))
    false_positive = selected - correct
    true_positive = selected & correct
    missed = correct - selected

    precision = len(true_positive) / len(selected) if selected else (1.0 if not correct else 0.0)
    recall = len(true_positive) / len(correct) if correct else 1.0
    wrong_rate = len(false_positive) / len(selected) if selected else 0.0

    expected_agents = _set(episode.get("correct_agent_runs"))
    selected_agents = _set(prediction.get("selected_agent_runs"))
    agent_recall = len(expected_agents & selected_agents) / len(expected_agents) if expected_agents else 1.0

    return {
        "episode_id": str(episode.get("episode_id") or ""),
        "resource_precision": round(precision, 6),
        "resource_recall": round(recall, 6),
        "wrong_resource_rate": round(wrong_rate, 6),
        "wrong_resource_count": len(false_positive),
        "missed_resource_count": len(missed),
        "agent_run_recall": round(agent_recall, 6),
        "clarification_questions": max(0.0, _number(prediction.get("clarification_questions"))),
        "wrong_assumptions": max(0.0, _number(prediction.get("wrong_assumptions"))),
        "continuation_success": bool(prediction.get("continuation_success")),
        "consequential_wrong_action": bool(prediction.get("consequential_wrong_action")),
    }


def aggregate(episodes: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(item.get("episode_id") or ""): item for item in predictions}
    rows = [
        score_episode(episode, by_id.get(str(episode.get("episode_id") or ""), {}))
        for episode in episodes
    ]
    if not rows:
        raise ValueError("no episodes")
    return {
        "episodes": len(rows),
        "resource_precision": round(mean(row["resource_precision"] for row in rows), 6),
        "resource_recall": round(mean(row["resource_recall"] for row in rows), 6),
        "wrong_resource_rate": round(mean(row["wrong_resource_rate"] for row in rows), 6),
        "wrong_resources_per_episode": round(mean(row["wrong_resource_count"] for row in rows), 6),
        "agent_run_recall": round(mean(row["agent_run_recall"] for row in rows), 6),
        "clarification_questions_per_episode": round(mean(row["clarification_questions"] for row in rows), 6),
        "wrong_assumptions_per_episode": round(mean(row["wrong_assumptions"] for row in rows), 6),
        "continuation_success_rate": round(mean(1.0 if row["continuation_success"] else 0.0 for row in rows), 6),
        "consequential_wrong_action_rate": round(mean(1.0 if row["consequential_wrong_action"] else 0.0 for row in rows), 6),
        "per_episode": rows,
    }


def decision(control: dict[str, Any], owg: dict[str, Any]) -> dict[str, Any]:
    """Pre-registered product decision, intentionally demanding a large delta."""
    recall_delta = float(owg["resource_recall"]) - float(control["resource_recall"])
    success_delta = float(owg["continuation_success_rate"]) - float(control["continuation_success_rate"])
    clarification_delta = float(control["clarification_questions_per_episode"]) - float(owg["clarification_questions_per_episode"])
    wrong_not_worse = float(owg["wrong_resource_rate"]) <= float(control["wrong_resource_rate"]) + 1e-12
    assumptions_not_worse = float(owg["wrong_assumptions_per_episode"]) <= float(control["wrong_assumptions_per_episode"]) + 1e-12
    consequential_not_worse = float(owg["consequential_wrong_action_rate"]) <= float(control["consequential_wrong_action_rate"]) + 1e-12
    safe = wrong_not_worse and assumptions_not_worse and consequential_not_worse

    large_resource_gain = recall_delta >= 0.15 and safe
    large_outcome_gain = success_delta >= 0.15 and safe
    major_clarification_gain = clarification_delta >= 0.50 and recall_delta >= 0.05 and safe
    continue_thesis = bool(large_resource_gain or large_outcome_gain or major_clarification_gain)

    return {
        "decision": "continue_continuity_thesis" if continue_thesis else "stop_or_pause_product_pivot",
        "resource_recall_delta": round(recall_delta, 6),
        "continuation_success_delta": round(success_delta, 6),
        "clarification_questions_reduction": round(clarification_delta, 6),
        "wrong_resource_rate_not_worse": wrong_not_worse,
        "wrong_assumptions_not_worse": assumptions_not_worse,
        "consequential_wrong_action_rate_not_worse": consequential_not_worse,
        "thresholds": {
            "resource_recall_gain": 0.15,
            "continuation_success_gain": 0.15,
            "clarification_reduction_with_minimum_recall_gain": {"clarifications": 0.50, "recall": 0.05},
            "wrong_resource_rate_must_not_increase": True,
            "wrong_assumptions_must_not_increase": True,
            "consequential_wrong_action_rate_must_not_increase": True,
        },
    }


def _jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}: every JSONL row must be an object")
        rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score OWG ambiguous-continuity A/B predictions")
    parser.add_argument("--episodes", required=True)
    parser.add_argument("--control", required=True, help="Predictions from the agent without OWG context")
    parser.add_argument("--owg", required=True, help="Predictions from the same agent with OWG continuity context")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    episodes = _jsonl(args.episodes)
    control = aggregate(episodes, _jsonl(args.control))
    owg = aggregate(episodes, _jsonl(args.owg))
    result = {"control": control, "owg": owg, "product_decision": decision(control, owg)}
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
