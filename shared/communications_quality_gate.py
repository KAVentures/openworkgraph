"""Optional fail-closed language-specific holdout gate for communications intent inference.

This evaluates ONLY reviewer-labelled holdout metrics. It does not establish
independent external validation, auto-detect the language of uploaded text,
authorize business actions, or change the default learned-classifier mode.
"""
from __future__ import annotations

import re
from typing import Any

MIN_POSITIVES = 5
MIN_NEGATIVES = 5
MIN_F1 = 0.80
MAX_UNCERTAIN_RATE = 0.35
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$")


class QualityGateError(ValueError):
    """Safe error; the uploaded message text is never included."""


def normalize_language(language: Any) -> str:
    if not isinstance(language, str) or not _LANGUAGE.fullmatch(language.strip().lower()):
        raise QualityGateError("Declare a language code such as en, sv or pt-BR to enable the holdout gate")
    return language.strip().lower()


def assess(evaluation: Any, taxonomy_intents: list[str], language: Any) -> dict[str, Any]:
    """Assess all declared taxonomy intents, including those not successfully trained.

    A global F1 does not establish a language-specific model's usability.
    Each intent must have labelled positive and negative examples in this
    language and meet fixed descriptive thresholds. No holdout text leaves
    this function and no model thresholds are fitted to the holdout.
    """
    language = normalize_language(language)
    rows = evaluation.get("intents") if isinstance(evaluation, dict) else None
    if not isinstance(rows, list):
        rows = []
    by_intent = {r["intent"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("intent"), str)}
    rejected = []
    for name in sorted(set(taxonomy_intents)):
        item = by_intent.get(name)
        if item is None:
            rejected.append({"intent": name, "reason": "intent_not_trained_or_not_evaluated"})
            continue
        languages = item.get("by_language")
        group = languages.get(language) if isinstance(languages, dict) else None
        if not isinstance(group, dict):
            rejected.append({"intent": name, "reason": "language_not_in_holdout"})
            continue
        count, positives = group.get("examples"), group.get("positive_examples")
        if (type(count) is not int or type(positives) is not int or
                positives < MIN_POSITIVES or count - positives < MIN_NEGATIVES):
            rejected.append({"intent": name, "reason": "insufficient_positive_or_negative_holdout_cases"})
            continue
        f1, uncertain = group.get("f1"), group.get("uncertain_rate")
        if (not isinstance(f1, (int, float)) or type(f1) is bool or
                not isinstance(uncertain, (int, float)) or type(uncertain) is bool or
                not (0 <= f1 <= 1 and 0 <= uncertain <= 1)):
            rejected.append({"intent": name, "reason": "holdout_quality_unavailable"})
        elif f1 < MIN_F1:
            rejected.append({"intent": name, "reason": "holdout_f1_below_gate"})
        elif uncertain > MAX_UNCERTAIN_RATE:
            rejected.append({"intent": name, "reason": "holdout_abstention_above_gate"})

    if not taxonomy_intents:
        rejected.append({"intent": None, "reason": "no_reviewed_intents"})
    return {
        "format": "openworkgraph.communications-language-quality-gate.v1",
        "declared_language": language,
        "ready": not rejected,
        "rejected_intents": rejected[:40],
        "rejected_intents_total": len(rejected),
        "evaluated_intents": len(set(taxonomy_intents)),
        "criteria": {"min_positive_holdout_cases_per_intent": MIN_POSITIVES,
                     "min_negative_holdout_cases_per_intent": MIN_NEGATIVES,
                     "min_f1_per_intent": MIN_F1,
                     "max_uncertain_rate_per_intent": MAX_UNCERTAIN_RATE},
        "language_inferred_from_upload": False,
        "quality_estimates_from_reviewer_labelled_holdout_only": True,
        "independent_external_quality_verified": False,
        "outcomes_or_reasons_for_contact_verified": False,
        "execution_authorized": False,
        "reason": ("All selected intent metrics meet the descriptive holdout checks"
                   if not rejected else "One or more intents have insufficient or weak language-specific holdout evidence"),
    }


__all__ = ["QualityGateError", "assess", "normalize_language"]
