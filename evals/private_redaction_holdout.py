"""Private holdout scoring contract for local-first Gateway privacy.

The holdout JSONL is NEVER included in this repository and must not be
disclosed to an agent working on the redactor. Only aggregate scores are
printed; no failing examples, PII, hashes of private strings or excerpts.
Use an independently controlled runner to avoid exposing the private
dataset as a PR-workflow secret.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from connector.cloud_privacy import DeviceUploadRedactor

MIN_PERSON_RECALL = 0.95
MIN_ORG_RECALL = 0.95
MIN_UI_PRESERVED = 0.95
# Every annotated medical/special-category span must be absent on release.
MIN_CLINICAL_RECALL = 1.0
MIN_HANDLE_RECALL = 1.0
MIN_CASES_FOR_RELEASE = 100
MIN_CLINICAL_CASES_FOR_RELEASE = 10
KINDS = ("PERSON", "ORG", "HANDLE", "CLINICAL")


def score(path: Path, *, secret: str, enforce_minimum: bool = True) -> dict:
    if not path.is_file():
        raise ValueError("holdout path does not exist")
    if not path.is_absolute():
        raise ValueError("use an absolute holdout path outside the checkout")
    # Prevent accidentally checking in or using a corpus alongside the
    # implementation being graded.
    root = Path(__file__).resolve().parents[1]
    if path.resolve().is_relative_to(root):
        raise ValueError("private holdout must be outside the source checkout")
    scorer = DeviceUploadRedactor(secret=secret)
    counts = Counter()
    success = Counter()
    cases = 0
    clinical_cases = 0

    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("holdout row must be a JSON object")
            raw = row.get("text")
            if not isinstance(raw, str) or not raw:
                raise ValueError("holdout text must not be empty")
            spans = row.get("sensitive_spans", [])
            if not isinstance(spans, list) or not isinstance(row.get("must_preserve", []), list):
                raise ValueError("invalid private holdout annotation")
            projected = scorer.label(raw)
            cases += 1
            clinical = row.get("category") == "clinical"
            clinical_cases += int(clinical)
            for item in spans:
                if not isinstance(item, dict):
                    raise ValueError("invalid sensitive span")
                kind = item.get("kind")
                value = item.get("value")
                if kind not in KINDS or not isinstance(value, str) or not value or value.casefold() not in raw.casefold():
                    raise ValueError("sensitive span kind/value mismatch")
                counts[kind] += 1
                recovered = value.casefold() not in projected.casefold()
                success[kind] += int(recovered)
                if clinical:
                    counts["CLINICAL"] += int(kind != "CLINICAL")
                    success["CLINICAL"] += int(recovered and kind != "CLINICAL")
            # UI preservation: prelabelled safe strings count only when
            # output remains identical; mixed labels can require fragments.
            if not spans:
                counts["UI"] += 1
                success["UI"] += int(projected == raw)
            else:
                for piece in row.get("must_preserve", []):
                    if not isinstance(piece, str) or not piece or piece not in raw:
                        raise ValueError("invalid required-preservation fragment")
                    counts["UI"] += 1
                    success["UI"] += int(piece in projected)

    thresholds = {
        "PERSON": MIN_PERSON_RECALL, "ORG": MIN_ORG_RECALL,
        "HANDLE": MIN_HANDLE_RECALL, "UI": MIN_UI_PRESERVED,
        "CLINICAL": MIN_CLINICAL_RECALL,
    }
    ratios = {k: (success[k] / counts[k] if counts[k] else None) for k in thresholds}
    pass_thresholds = all(
        counts[k] > 0 and ratios[k] >= minimum
        for k, minimum in thresholds.items()
    )
    samples_ok = cases >= MIN_CASES_FOR_RELEASE and clinical_cases >= MIN_CLINICAL_CASES_FOR_RELEASE
    return {
        "private_holdout": True,
        "dataset_contents_logged": False,
        "case_count": cases, "clinical_case_count": clinical_cases,
        "metric_counts": {k: counts[k] for k in thresholds},
        "scores": {k: round(v, 4) if v is not None else None for k, v in ratios.items()},
        "thresholds": thresholds,
        "minimum_sample_gate": samples_ok,
        "release_gate_passed": pass_thresholds and samples_ok if enforce_minimum else pass_thresholds,
        "clinical_special_category_zero_misses_required": True,
        "limitations": [
            "String-span suppression is not proof of anonymization",
            "An independently annotated sample is not an unbiased population estimate",
            "Model-facing nested fields and past Gateway data need separate audits",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", default=os.environ.get("OWG_PRIVATE_HOLDOUT_PATH", ""))
    args = parser.parse_args()
    if not args.holdout:
        print("OWG_PRIVATE_HOLDOUT_PATH is required", file=sys.stderr)
        return 2
    try:
        result = score(
            Path(args.holdout), secret="private-holdout-evaluation-key-not-a-customer-secret",
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["release_gate_passed"] else 1
    except Exception as exc:
        # Never print exception messages, which may include private input.
        print("holdout scoring failed without disclosing private examples: " +
              type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
