from __future__ import annotations

import argparse
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
    prediction["case_id"] = case_id
    workflows = prediction.get("workflows")
    if not isinstance(workflows, list):
        raise ValueError("workflows must be a list")
    seen: set[str] = set()
    for idx, workflow in enumerate(workflows, start=1):
        if not isinstance(workflow, dict):
            raise ValueError(f"workflow {idx} must be an object")
        workflow.setdefault("prediction_id", f"workflow_{idx}")
        for key in ("event_ids", "ordered_event_ids", "automation_relevant_event_ids", "ui_mechanic_event_ids"):
            values = workflow.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"{key} must be a list")
            bad = {str(x) for x in values} - event_ids
            if bad:
                raise ValueError(f"{key} contains unknown event ids: {sorted(bad)}")
        members = {str(x) for x in workflow.get("event_ids", [])}
        overlap = seen & members
        if overlap:
            raise ValueError(f"events assigned to multiple workflows: {sorted(overlap)}")
        seen |= members
    unassigned = prediction.get("unassigned_event_ids", [])
    if not isinstance(unassigned, list):
        raise ValueError("unassigned_event_ids must be a list")
    bad = {str(x) for x in unassigned} - event_ids
    if bad:
        raise ValueError(f"unassigned_event_ids contains unknown ids: {sorted(bad)}")
    prediction["insufficient_evidence"] = bool(prediction.get("insufficient_evidence", False))
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
        for line in predictions_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[str(row["case_id"])] = row

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
