from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .prepare import SYSTEM_INSTRUCTION, split_cases
from .fixtures import load_cases
from .scoring import acceptance, aggregate

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_KEY_ENV = "OPENAI_API_KEY"


def _extract_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("model response must be a JSON object")
    return parsed


def _validate_prediction(prediction: dict[str, Any], case_id: str, event_ids: set[str]) -> dict[str, Any]:
    # A response that names another case must never be silently relabeled and
    # scored as this case. Older model outputs may omit case_id entirely.
    if "case_id" in prediction and prediction["case_id"] != case_id:
        raise ValueError("model response case_id does not match requested case")
    prediction["case_id"] = case_id
    workflows = prediction.get("workflows")
    if not isinstance(workflows, list):
        raise ValueError("workflows must be a list")
    def checked_ids(values: Any, key: str) -> set[str]:
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a list")
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"{key} requires nonempty string event ids")
        members = set(values)
        if len(members) != len(values):
            raise ValueError(f"{key} contains duplicate event ids")
        bad = members - event_ids
        if bad:
            raise ValueError(f"{key} contains unknown event ids: {sorted(bad)}")
        return members

    seen: set[str] = set()
    prediction_ids: set[str] = set()
    for idx, workflow in enumerate(workflows, start=1):
        if not isinstance(workflow, dict):
            raise ValueError(f"workflow {idx} must be an object")
        workflow.setdefault("prediction_id", f"workflow_{idx}")
        prediction_id = workflow["prediction_id"]
        if not isinstance(prediction_id, str) or not prediction_id:
            raise ValueError(f"workflow {idx} requires a nonempty string prediction_id")
        if prediction_id in prediction_ids:
            raise ValueError(f"duplicate workflow prediction_id: {prediction_id}")
        prediction_ids.add(prediction_id)
        members = checked_ids(workflow.get("event_ids", []), "event_ids")
        for key in ("ordered_event_ids", "automation_relevant_event_ids", "ui_mechanic_event_ids"):
            submembers = checked_ids(workflow.get(key, []), key)
            if not submembers <= members:
                raise ValueError(f"{key} contains events outside its workflow event_ids")
        overlap = seen & members
        if overlap:
            raise ValueError(f"events assigned to multiple workflows: {sorted(overlap)}")
        seen |= members
    unassigned = checked_ids(prediction.get("unassigned_event_ids", []), "unassigned_event_ids")
    if seen & unassigned:
        raise ValueError("events cannot be both assigned and unassigned")
    # These values feed the preregistered uncertainty gate. Python's bool()
    # would misread JSON strings such as "false" as True and overstate caution.
    insufficient = prediction.get("insufficient_evidence", False)
    if not isinstance(insufficient, bool):
        raise ValueError("insufficient_evidence must be a boolean")
    prediction["insufficient_evidence"] = insufficient
    if "uncertainty" in prediction:
        uncertainty = prediction["uncertainty"]
        if not isinstance(uncertainty, dict):
            raise ValueError("uncertainty must be an object")
        for dimension in ("membership", "business_rule", "outcome"):
            if dimension in uncertainty and not isinstance(uncertainty[dimension], bool):
                raise ValueError(f"uncertainty.{dimension} must be a boolean")
    return prediction


def _request(client: httpx.Client, *, base_url: str, api_key: str, model: str, case: dict[str, Any],
             temperature: float | None, timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    url = base_url.rstrip("/") + "/chat/completions"
    user_payload = {
        "case_id": case["case_id"],
        "presented_evidence": case["presented_evidence"],
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
        ],
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        body["temperature"] = temperature
    response = client.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    raw = response.json()
    content = raw["choices"][0]["message"]["content"]
    prediction = _extract_json(content)
    event_ids = {str(e["event_id"]) for e in case["presented_evidence"]}
    return _validate_prediction(prediction, case["case_id"], event_ids), {
        "response_id": raw.get("id"),
        "response_model": raw.get("model"),
        "usage": raw.get("usage"),
    }


def _revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _load_resume_predictions(
    path: Path,
    selected_case_ids: set[str],
    *,
    evidence_ids_by_case: dict[str, set[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Read an interrupted run without silently replacing or importing cases.

    When current blind evidence is supplied, validate every resumed prediction
    against its selected case before the runner makes another provider request.
    This is not a model/revision attestation or an answer-key check.
    """
    existing: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"resume predictions line {line_number} must be an object")
        cid = row.get("case_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError(f"resume predictions line {line_number} has invalid case_id")
        if cid not in selected_case_ids:
            raise ValueError(f"resume predictions contain case outside selected split: {cid}")
        if cid in existing:
            raise ValueError(f"resume predictions contain duplicate case_id: {cid}")
        if evidence_ids_by_case is not None:
            current_ids = evidence_ids_by_case.get(cid)
            if current_ids is None:
                raise ValueError(f"resume predictions lack current evidence for case_id: {cid}")
            # The validator supplies optional defaults in place. Validate a
            # copy so the exact persisted JSONL row remains unmodified.
            try:
                _validate_prediction(deepcopy(row), cid, current_ids)
            except ValueError as exc:
                raise ValueError(f"resume predictions line {line_number} invalid for current evidence: {exc}") from exc
        existing[cid] = row
    return existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the blind OWG reconstruction eval through an OpenAI-compatible endpoint")
    parser.add_argument("--model", required=True, help="exact model ID accepted by the endpoint")
    parser.add_argument("--base-url", default=os.getenv("OWG_EVAL_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default=os.getenv("OWG_EVAL_API_KEY_ENV", DEFAULT_KEY_ENV))
    parser.add_argument("--output-dir", default="evals/reconstruction/results")
    parser.add_argument("--temperature", type=float, help="optional sampling temperature; omitted by default for reasoning-model compatibility")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all",
                        help="family-level split; use holdout only for final evaluation")
    parser.add_argument("--max-cases", type=int, help="debug only; omitted means the full selected split")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        parser.error(f"missing credential environment variable {args.api_key_env}; never put API keys in repo files")

    cases = split_cases(load_cases(), args.split)
    if args.max_cases is not None:
        if args.max_cases < 1:
            parser.error("--max-cases must be >= 1")
        cases = cases[: args.max_cases]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.model)
    split_suffix = "" if args.split == "all" else f".{args.split}"
    predictions_path = out_dir / f"{safe_model}{split_suffix}.predictions.jsonl"
    report_path = out_dir / f"{safe_model}{split_suffix}.report.json"
    metadata_path = out_dir / f"{safe_model}{split_suffix}.metadata.json"

    existing: dict[str, dict[str, Any]] = {}
    if args.resume and predictions_path.exists():
        existing = _load_resume_predictions(
            predictions_path,
            {str(case["case_id"]) for case in cases},
            evidence_ids_by_case={
                str(case["case_id"]): {str(event["event_id"]) for event in case["presented_evidence"]}
                for case in cases
            },
        )

    predictions: list[dict[str, Any]] = []
    response_meta: list[dict[str, Any]] = []
    started = time.time()
    mode = "a" if args.resume else "w"
    with httpx.Client() as client, predictions_path.open(mode, encoding="utf-8") as fh:
        for index, case in enumerate(cases, start=1):
            cid = str(case["case_id"])
            if cid in existing:
                predictions.append(existing[cid])
                continue
            prediction, meta = _request(
                client,
                base_url=args.base_url,
                api_key=api_key,
                model=args.model,
                case=case,
                temperature=args.temperature,
                timeout=args.timeout,
            )
            predictions.append(prediction)
            response_meta.append({"case_id": cid, **meta})
            fh.write(json.dumps(prediction, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            print(f"[{index}/{len(cases)}] {cid}", file=sys.stderr)

    by_id = {str(row["case_id"]): row for row in predictions}
    ordered = [by_id[str(case["case_id"])] for case in cases if str(case["case_id"]) in by_id]
    report = aggregate(cases, ordered)
    result = {"metrics": report, "acceptance": acceptance(report)}
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    metadata = {
        "model_requested": args.model,
        "base_url": args.base_url,
        "temperature": args.temperature,
        "split": args.split,
        "cases": len(cases),
        "revision": _revision(),
        "elapsed_seconds": round(time.time() - started, 3),
        "responses": response_meta,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
