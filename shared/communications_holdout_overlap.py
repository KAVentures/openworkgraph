"""Bounded local lexical overlap diagnostic for communications holdouts.

No text, signatures, or identifiers are returned in the diagnostic report.
The subset is only free of *detected lexical overlap*, not independently
sampled, thread-disjoint, semantically disjoint or externally validated.
"""
from __future__ import annotations

from collections import Counter
import re
import unicodedata
from typing import Any

FORMAT = "openworkgraph.communications-holdout-overlap.v1"
MAX_EXAMPLES = 2000
MAX_TEXT = 4000
MAX_WORK = 20_000_000
MIN_SHINGLES = 5
_WORD = re.compile(r"\w+", re.UNICODE)
_DIGITS = re.compile(r"\d+")


def _signature(text: str):
    # Case, punctuation and changing numeric references must not turn a
    # repeated template into apparently independent evidence.
    tokens = tuple(_DIGITS.sub("0", w) for w in
                   _WORD.findall(unicodedata.normalize("NFKC", text).casefold()))
    grams = frozenset(zip(tokens, tokens[1:], tokens[2:]))
    return tokens, grams


def audit(training: list[dict[str, Any]], holdout: list[dict[str, Any]],
          *, max_work: int = MAX_WORK) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return aggregate diagnostics and the internal eligible subset.

    Exact normalized matches take precedence over 80% word-trigram Jaccard.
    If the deterministic work budget is exceeded, suppress the entire
    eligible subset instead of calling a partly checked sample clean.
    """
    if type(max_work) is not int or not 0 <= max_work <= MAX_WORK:
        raise ValueError("invalid overlap audit work budget")
    training = [e for e in training if isinstance(e.get("labels"), list)]
    holdout = [e for e in holdout if isinstance(e.get("labels"), list)]
    total = len(holdout)

    def report(complete, counts, languages, eligible):
        return {
            "format": FORMAT, "complete": complete,
            "training_examples": len(training), "holdout_examples": total,
            "normalized_exact_overlap": counts["exact"] if complete else None,
            "near_lexical_overlap": counts["near"] if complete else None,
            "no_detected_overlap": len(eligible) if complete else None,
            "by_language": {
                language: {"holdout_examples": sum(c.values()),
                           "normalized_exact_overlap": c["exact"],
                           "near_lexical_overlap": c["near"],
                           "no_detected_overlap": c["eligible"]}
                for language, c in sorted(languages.items())
            } if complete else {},
            "method": "normalized_word_sequence_or_word_trigram_jaccard_ge_0_8",
            "minimum_distinct_trigrams_for_near_match": MIN_SHINGLES,
            "numeric_runs_normalized": True,
            "thread_or_semantic_independence_verified": False,
            "external_validation_verified": False,
        }, eligible if complete else []

    counts = Counter()
    languages: dict[str, Counter] = {}
    if len(training) + total > MAX_EXAMPLES or any(
        not isinstance(e.get("text"), str) or len(e["text"]) > MAX_TEXT
        for e in training + holdout
    ):
        return report(False, counts, languages, [])
    signatures = [_signature(e["text"]) for e in training]
    exact = {tokens for tokens, _ in signatures}
    # Deduplicate identical training trigram sets. Length buckets avoid
    # intersections that cannot reach the specified Jaccard threshold.
    by_size: dict[int, set] = {}
    for _, grams in signatures:
        if len(grams) >= MIN_SHINGLES:
            by_size.setdefault(len(grams), set()).add(grams)
    # Canonical order ensures that the bounded result does not depend on hash
    # randomization or the caller's ordering of training examples.
    buckets = {size: sorted(groups, key=lambda g: sorted(g))
               for size, groups in sorted(by_size.items())}
    work = 0
    eligible = []
    for example in holdout:
        tokens, grams = _signature(example["text"])
        kind = "eligible"
        if tokens in exact:
            kind = "exact"
        elif len(grams) >= MIN_SHINGLES:
            n = len(grams)
            for size, candidates in buckets.items():
                if 5 * min(size, n) < 4 * max(size, n):
                    continue
                for other in candidates:
                    work += min(size, n)
                    if work > max_work:
                        return report(False, counts, languages, [])
                    intersection = len(grams & other)
                    if 5 * intersection >= 4 * (size + n - intersection):
                        kind = "near"
                        break
                if kind == "near":
                    break
        counts[kind] += 1
        languages.setdefault(example.get("language") or "und", Counter())[kind] += 1
        if kind == "eligible":
            eligible.append(example)
    return report(True, counts, languages, eligible)
