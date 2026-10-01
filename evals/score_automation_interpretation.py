from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "automation_interpretation_cases.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalized(values: list[int]) -> float:
    if not values:
        return 0.0
    return round(100.0 * sum(values) / (2 * len(values)), 1)


def score(score_path: Path) -> dict[str, Any]:
    corpus = _load(CASES_PATH)
    submitted = _load(score_path)
    expected = {case["id"]: case for case in corpus["cases"]}
    received = {case["id"]: case for case in submitted.get("cases", [])}

    missing = sorted(set(expected) - set(received))
    extra = sorted(set(received) - set(expected))
    if missing or extra:
        raise ValueError(f"case mismatch: missing={missing}, extra={extra}")

    axis_values: dict[str, list[int]] = {"underestimation": [], "overreach": []}
    all_values: list[int] = []
    critical_failures: list[str] = []
    case_scores: list[dict[str, Any]] = []

    for case_id, spec in expected.items():
        result = received[case_id]
        cover = list(result.get("must_cover", []))
        avoid = list(result.get("must_not", []))
        if len(cover) != len(spec["must_cover"]):
            raise ValueError(f"{case_id}: expected {len(spec['must_cover'])} must_cover scores, got {len(cover)}")
        if len(avoid) != len(spec["must_not"]):
            raise ValueError(f"{case_id}: expected {len(spec['must_not'])} must_not scores, got {len(avoid)}")
        values = cover + avoid
        if any(value not in (0, 1, 2) for value in values):
            raise ValueError(f"{case_id}: scores must be 0, 1, or 2")

        axis = spec["bias_axis"]
        axis_values[axis].extend(values)
        all_values.extend(values)
        for index, value in enumerate(avoid):
            if value == 0:
                critical_failures.append(f"{case_id}:must_not[{index}]")

        case_scores.append({
            "id": case_id,
            "bias_axis": axis,
            "score": _normalized(values),
        })

    return {
        "model": submitted.get("model"),
        "client": submitted.get("client"),
        "revision": submitted.get("revision"),
        "underestimation_score": _normalized(axis_values["underestimation"]),
        "overreach_score": _normalized(axis_values["overreach"]),
        "total_score": _normalized(all_values),
        "critical_failures": critical_failures,
        "case_scores": case_scores,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) != 1:
        print("usage: python evals/score_automation_interpretation.py <scores.json>", file=sys.stderr)
        return 2
    try:
        report = score(Path(args[0]))
    except Exception as exc:
        print(f"score error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
