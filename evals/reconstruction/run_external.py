from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import hmac
import json
import math
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
BLIND_EVIDENCE_DIGEST_FIELD = "_owg_blind_evidence_sha256"


def _blind_evidence_digest(case_id: str, presented_evidence: list[dict[str, Any]]) -> str:
    """Bind a prediction to exact *blind* evidence; never hash answer-key truth."""
    payload = json.dumps(
        {"case_id": case_id, "presented_evidence": presented_evidence},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(b"owg-reconstruction-blind-evidence-v1\0" + payload).hexdigest()



def _selected_blind_corpus_digest(cases: list[dict[str, Any]]) -> str:
    """Fingerprint the complete selected *blind* corpus, never the answer key.

    Preserve case and evidence-array order; normalize only JSON object-key
    order. Call after selected-case shape/finite-JSON preflight.
    """
    blind = [
        {"case_id": case["case_id"], "presented_evidence": case["presented_evidence"]}
        for case in cases
    ]
    payload = json.dumps(
        blind, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        b"owg-reconstruction-selected-blind-corpus-v1\0" + payload
    ).hexdigest()


def _selected_prediction_set_digest(predictions: list[dict[str, Any]]) -> str:
    """Fingerprint the complete persisted prediction assignments, not the truth.

    The caller supplies rows in selected-case order, so JSONL row order does
    not affect scoring or this digest. JSON object-key order is normalized,
    but workflow and event-array order is retained.
    """
    payload = json.dumps(
        predictions, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        b"owg-reconstruction-selected-prediction-set-v1\\0".replace(b"\\0", b"\0") + payload
    ).hexdigest()


def _strict_json_loads(text: str) -> Any:
    """Refuse ambiguous duplicate keys at every JSON object depth.

    Standard json.loads silently keeps the final value, which can hide
    contradictory case IDs, event assignments, digests or response receipts.
    Repeated field names in *different* objects remain legitimate.
    """
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    def finite_float(token: str) -> float:
        value = float(token)
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number is not allowed")
        return value

    def reject_nonfinite_constant(token: str) -> float:
        # Python accepts NaN/Infinity by default, though JSON does not.
        raise ValueError(f"non-finite JSON constant is not allowed: {token}")

    return json.loads(
        text, object_pairs_hook=unique_object,
        parse_constant=reject_nonfinite_constant, parse_float=finite_float,
    )


def _extract_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    parsed = _strict_json_loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("model response must be a JSON object")
    return parsed


def _validate_prediction(
    prediction: dict[str, Any], case_id: str, event_ids: set[str],
    *, allow_runner_digest: bool = False,
) -> dict[str, Any]:
    # This provenance field is added by the local runner, never by a model.
    if BLIND_EVIDENCE_DIGEST_FIELD in prediction and not allow_runner_digest:
        raise ValueError("model response must not supply runner blind-evidence digest")
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
    # Parse the provider envelope itself strictly, not only its nested model
    # content. Response.json() accepts duplicate keys and non-finite numbers,
    # which can corrupt persisted usage/model receipts even when the model
    # content is otherwise valid.
    raw = _strict_json_loads(response.text)
    if not isinstance(raw, dict):
        raise ValueError("provider completion envelope must be a JSON object")
    content = raw["choices"][0]["message"]["content"]
    prediction = _extract_json(content)
    event_ids = {str(e["event_id"]) for e in case["presented_evidence"]}
    validated = _validate_prediction(prediction, case["case_id"], event_ids)
    validated[BLIND_EVIDENCE_DIGEST_FIELD] = _blind_evidence_digest(
        case["case_id"], case["presented_evidence"]
    )
    return validated, {
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
    evidence_by_case: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Read an interrupted run without silently replacing or importing cases.

    When current blind evidence is supplied, validate every resumed prediction
    against its selected case before the runner makes another provider request.
    New runner rows also bind exact blind evidence content, not only event IDs.
    Legacy rows without the digest remain accepted but are not content-attested.
    This is not a model/revision attestation or an answer-key check.
    """
    existing: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = _strict_json_loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"resume predictions line {line_number} must be an object")
        cid = row.get("case_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError(f"resume predictions line {line_number} has invalid case_id")
        if cid not in selected_case_ids:
            raise ValueError(f"resume predictions contain case outside selected split: {cid}")
        if cid in existing:
            raise ValueError(f"resume predictions contain duplicate case_id: {cid}")
        if BLIND_EVIDENCE_DIGEST_FIELD in row:
            supplied = row[BLIND_EVIDENCE_DIGEST_FIELD]
            if not isinstance(supplied, str) or re.fullmatch(r"[0-9a-f]{64}", supplied) is None:
                raise ValueError(f"resume predictions line {line_number} has malformed blind-evidence digest")
            if evidence_by_case is None or cid not in evidence_by_case:
                raise ValueError(f"resume predictions lack blind evidence for digest verification: {cid}")
            expected = _blind_evidence_digest(cid, evidence_by_case[cid])
            if not hmac.compare_digest(supplied, expected):
                raise ValueError(f"resume predictions line {line_number} blind evidence changed for case_id: {cid}")
        if evidence_ids_by_case is not None:
            current_ids = evidence_ids_by_case.get(cid)
            if current_ids is None:
                raise ValueError(f"resume predictions lack current evidence for case_id: {cid}")
            # The validator supplies optional defaults in place. Validate a
            # copy so the exact persisted JSONL row remains unmodified.
            try:
                _validate_prediction(deepcopy(row), cid, current_ids, allow_runner_digest=True)
            except ValueError as exc:
                raise ValueError(f"resume predictions line {line_number} invalid for current evidence: {exc}") from exc
        existing[cid] = row
    return existing



def _load_resume_metadata(
    path: Path, expected: dict[str, Any], resumed_case_ids: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    """Verify a prior completed-run manifest before mixing resumed predictions.

    Interrupted runs may have no manifest because it is written only after
    scoring. Those rows remain usable but must be reported as unattested.
    """
    if not path.exists():
        return [], True
    metadata = _strict_json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("resume metadata must be an object")
    legacy_digest_unattested = False
    for field, requested in expected.items():
        if field in ("selected_blind_corpus_sha256", "selected_prediction_set_sha256") and field not in metadata:
            # Historical manifests predate these fields. Keep resumes usable,
            # but never claim full corpus/prediction provenance for them.
            legacy_digest_unattested = True
            continue
        prior = metadata.get(field)
        if field not in metadata or type(prior) is not type(requested) or prior != requested:
            raise ValueError(f"resume metadata mismatch for {field}; use a new output directory")
    responses = metadata.get("responses")
    if not isinstance(responses, list):
        raise ValueError("resume metadata responses must be a list")
    seen: set[str] = set()
    for row in responses:
        if not isinstance(row, dict):
            raise ValueError("resume metadata response must be an object")
        cid = row.get("case_id")
        if not isinstance(cid, str) or not cid or cid not in resumed_case_ids:
            raise ValueError("resume metadata response refers to an invalid or absent case")
        if cid in seen:
            raise ValueError(f"resume metadata contains duplicate response case_id: {cid}")
        seen.add(cid)
    prior_unattested = metadata.get("resume_provenance_unverified", False)
    if not isinstance(prior_unattested, bool):
        raise ValueError("resume_provenance_unverified must be a boolean")
    # Old runner versions discarded prior receipts on each resume. Do not
    # fabricate receipts or silently claim complete response provenance.
    return (
        deepcopy(responses),
        prior_unattested or seen != resumed_case_ids or legacy_digest_unattested,
    )



def _validate_blind_json_shape(value: Any) -> None:
    """Reject Python-only values and ambiguous object keys in blind evidence."""
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            _validate_blind_json_shape(child)
    elif type(value) is list:
        for child in value:
            _validate_blind_json_shape(child)
    elif value is None or type(value) in (str, bool, int):
        return
    elif type(value) is float and math.isfinite(value):
        return
    else:
        raise ValueError("unsupported or non-finite JSON value")


def _validate_selected_blind_cases(cases: list[dict[str, Any]]) -> None:
    """Reject ambiguous selected evidence before a paid request or artifact write.

    Event IDs are case-local; equal IDs in *different* cases are legitimate.
    Only blind evidence is inspected, never ground_truth or answer-key fields.
    """
    if not cases:
        raise ValueError("selected reconstruction corpus must contain at least one case")
    seen_cases: set[str] = set()
    for case_index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"selected case {case_index} must be an object")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"selected case {case_index} requires a nonempty string case_id")
        if case_id in seen_cases:
            raise ValueError(f"duplicate selected case_id at position {case_index}")
        seen_cases.add(case_id)
        evidence = case.get("presented_evidence")
        if not isinstance(evidence, list):
            raise ValueError(f"selected case {case_index} presented_evidence must be a list")
        seen_events: set[str] = set()
        for event_index, event in enumerate(evidence, start=1):
            if not isinstance(event, dict):
                raise ValueError(
                    f"selected case {case_index} event {event_index} must be an object"
                )
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError(
                    f"selected case {case_index} event {event_index} requires a nonempty string event_id"
                )
            if event_id in seen_events:
                raise ValueError(
                    f"selected case {case_index} contains duplicate event_id at position {event_index}"
                )
            seen_events.add(event_id)
        # The digest used to bind persisted predictions is intentionally
        # strict, but computing it only after a provider response can waste a
        # paid request on malformed evidence. Preflight *all* selected cases
        # before output creation or network access, including legacy resumes.
        try:
            _validate_blind_json_shape(evidence)
            _blind_evidence_digest(case_id, evidence)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            # Do not echo potentially sensitive evidence into diagnostics.
            raise ValueError(
                f"selected case {case_index} blind evidence must be canonical finite JSON"
            ) from exc


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
    # Validate request options before reading cases, creating output artifacts
    # or sending a potentially billable provider request.
    if args.temperature is not None and (
        not math.isfinite(args.temperature) or args.temperature < 0
    ):
        parser.error("--temperature must be finite and >= 0")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and > 0")

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        parser.error(f"missing credential environment variable {args.api_key_env}; never put API keys in repo files")

    cases = split_cases(load_cases(), args.split)
    if args.max_cases is not None:
        if args.max_cases < 1:
            parser.error("--max-cases must be >= 1")
        cases = cases[: args.max_cases]

    _validate_selected_blind_cases(cases)
    selected_blind_corpus_sha256 = _selected_blind_corpus_digest(cases)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.model)
    split_suffix = "" if args.split == "all" else f".{args.split}"
    predictions_path = out_dir / f"{safe_model}{split_suffix}.predictions.jsonl"
    report_path = out_dir / f"{safe_model}{split_suffix}.report.json"
    metadata_path = out_dir / f"{safe_model}{split_suffix}.metadata.json"

    existing: dict[str, dict[str, Any]] = {}
    response_meta: list[dict[str, Any]] = []
    resume_provenance_unverified = False
    if args.resume and predictions_path.exists():
        existing = _load_resume_predictions(
            predictions_path,
            {str(case["case_id"]) for case in cases},
            evidence_ids_by_case={
                str(case["case_id"]): {str(event["event_id"]) for event in case["presented_evidence"]}
                for case in cases
            },
            evidence_by_case={
                str(case["case_id"]): case["presented_evidence"] for case in cases
            },
        )
        if existing:
            response_meta, resume_provenance_unverified = _load_resume_metadata(
                metadata_path,
                {
                    "model_requested": args.model,
                    "base_url": args.base_url,
                    "temperature": args.temperature,
                    "split": args.split,
                    "cases": len(cases),
                    "selected_blind_corpus_sha256": selected_blind_corpus_sha256,
                    "selected_prediction_set_sha256": _selected_prediction_set_digest(
                        [existing[str(case["case_id"])] for case in cases
                         if str(case["case_id"]) in existing]
                    ),
                },
                set(existing),
            )

    # Do not overwrite a prior manifest whose prediction rows disappeared.
    if args.resume and metadata_path.exists() and not existing:
        raise ValueError(
            "resume metadata exists without matching prediction rows; "
            "use a new output directory"
        )

    # A valid final JSON object without a newline is readable, but append
    # mode would concatenate the next JSON object onto it. Fail before calls.
    if (
        args.resume and predictions_path.exists()
        and predictions_path.stat().st_size > 0 and len(existing) < len(cases)
    ):
        with predictions_path.open("rb") as prior_file:
            prior_file.seek(-1, os.SEEK_END)
            if prior_file.read(1) != b"\n":
                raise ValueError(
                    "resume predictions file must end with a newline before appending; "
                    "repair the JSONL boundary or use a new output directory"
                )

    predictions: list[dict[str, Any]] = []
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
        "selected_blind_corpus_sha256": selected_blind_corpus_sha256,
        "selected_prediction_set_sha256": _selected_prediction_set_digest(ordered),
        "revision": _revision(),
        "elapsed_seconds": round(time.time() - started, 3),
        "responses": response_meta,
        "resume_provenance_unverified": resume_provenance_unverified,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
