"""Optional local-only comparison of spaCy entity detection with hosted rules.

Runs exclusively on the repo's synthetic regression corpus. It performs no
network requests and is NOT imported by the hosted Gateway or MCP process.
Install a compatible spaCy release and the language model yourself, then run:
  python -m evals.optional_spacy_ner_audit --model sv_core_news_sm

Do not interpret one synthetic score as authorization to deploy a third-party
model over private business data.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

_CORPUS = (Path(__file__).resolve().parents[1] / "tests" / "fixtures" /
           "hosted_redaction_adversarial.json")
_PERSON_LABELS = {"PERSON", "PER"}
_ORG_LABELS = {"ORG"}


def evaluate(model_name: str) -> dict:
    import spacy  # optional: intentionally not required by pyproject or runtime

    corpus = json.loads(_CORPUS.read_text(encoding="utf-8"))
    nlp = spacy.load(model_name)
    records = corpus["sensitive"]
    negatives = corpus["preserve"]
    started = time.perf_counter()
    predictions = list(nlp.pipe([row["text"] for row in records] + negatives))
    elapsed = (time.perf_counter() - started) * 1000
    found = 0
    missed: list[str] = []
    for row, doc in zip(records, predictions):
        labels = _PERSON_LABELS if row["kind"] == "person" else _ORG_LABELS
        ok = any(ent.label_.upper() in labels
                 and row["span"].casefold() in ent.text.casefold()
                 for ent in doc.ents)
        found += int(ok)
        if not ok:
            missed.append(row["id"])
    # These sentences are expected to be preserved by OWG; NER annotations
    # may be legitimate, but aggressive automatic masking would lose context.
    utility_risk = [{
        "text": raw,
        "entities": [{"text": ent.text, "label": ent.label_} for ent in doc.ents],
    } for raw, doc in zip(negatives, predictions[len(records):]) if doc.ents]
    return {
        "model": model_name, "synthetic_only": True, "count": len(records),
        "span_and_type_recall": round(found / len(records), 4),
        "missed_case_ids": missed,
        "nonprivate_labels_with_entities": utility_risk,
        "nonnative_performance_not_measured": True,
        "total_inference_ms": round(elapsed, 1),
        "mean_inference_ms_per_label": round(elapsed / len(predictions), 2),
        "deployment_decision": "evaluate on held-out, consented real UI text first",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="sv_core_news_sm")
    args = parser.parse_args()
    try:
        result = evaluate(args.model)
    except (ImportError, OSError) as exc:
        raise SystemExit(
            f"Optional spaCy model unavailable: {exc}. "
            "Install spaCy and the compatible pipeline outside production."
        ) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
