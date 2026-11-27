"""Development-only keyword taxonomy change review.

The existing classifier is used unchanged. This is a retrospective comparison
on already retained training examples, not held-out or external validation.
No raw text, sample IDs, or per-message predictions leave this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from typing import Any

from . import communications_mining as mining

FORMAT = "owg.communications-taxonomy-change.v1"
MAX_CHARACTERS = 2_000_000
MAX_WORK = 45_000_000
RESERVED = {"ambiguous", "unclassified", "multiple intents", "uncertain", "outbound reply"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate(current, candidate):
    """Terms-only comparison: new/retired labels have no comparable truth."""
    for spec in (current, candidate):
        if not isinstance(spec, dict) or set(spec) != {"format", "intents"}:
            raise mining.CommunicationsError("taxonomy has unsupported fields")
    before, after = mining.parse_taxonomy(current), mining.parse_taxonomy(candidate)
    names = sorted(i.name for i in before)
    if names != sorted(i.name for i in after):
        raise mining.CommunicationsError("preview requires the same exact intent names; review label migrations separately")
    if any(n.lower() in RESERVED for n in names):
        raise mining.CommunicationsError("preview does not support reserved intent names")
    return before, after, names


def _safe_examples(examples, names, before, after):
    """Strictly select labelled training rows; held-out text is never scored."""
    if len(examples) > 2000:
        raise mining.CommunicationsError("review population exceeds supported limit")
    chosen = []
    chars = 0
    longest = max(len(p) for i in before + after for p in i.phrases)
    ids = set()
    for example in examples:
        if example.get("split") != "train" or example.get("labels") is None:
            continue
        labels = example["labels"]
        text = example.get("text")
        eid = example.get("example_id")
        if (not isinstance(labels, list) or not all(isinstance(x, str) and x in names for x in labels)
                or len(set(labels)) != len(labels)
                or not isinstance(text, str) or len(text) > 4000
                or not isinstance(eid, str) or not eid or eid in ids
                or not isinstance(example.get("language"), str)
                or not isinstance(example.get("expires_at"), str)):
            raise mining.CommunicationsError("reviewed training evidence contains invalid or retired labels; review it first")
        ids.add(eid)
        chars += len(text)
        if chars > MAX_CHARACTERS or chars * longest * 2 > MAX_WORK:
            raise mining.CommunicationsError("preview exceeds its work budget; no partial report is available")
        chosen.append(example)
    if not chosen:
        raise mining.CommunicationsError("label at least one training example before previewing")
    return sorted(chosen, key=lambda e: e["example_id"])


def snapshot(current, candidate, examples, settings):
    before, after, names = validate(current, candidate)
    chosen = _safe_examples(examples, names, before, after)
    # Private digest binds exact reviewed basis. Holdout rows do not contribute.
    basis = digest({"settings": settings, "rows": [
        {k: e[k] for k in ("example_id", "text", "labels", "language", "split", "expires_at")}
        for e in chosen]})
    current_hash, candidate_hash = digest(current), digest(candidate)
    return before, after, names, chosen, {
        "current_taxonomy_sha256": current_hash,
        "candidate_taxonomy_sha256": candidate_hash,
        "training_basis_sha256": basis,
        "preview_sha256": digest([FORMAT, current_hash, candidate_hash, basis]),
    }


def _outcome(text, intents):
    winner, scores = mining.classify(text, intents)
    tied = frozenset(k for k, v in scores.items() if v == max(scores.values(), default=0)) if winner == "ambiguous" else frozenset()
    return winner, tied


def _decisions(outcome, names):
    winner, tied = outcome
    return {n: ("uncertain" if n in tied else "present" if n == winner else "absent") for n in names}


def _state(decisions, truth):
    if any((d == "present") != (n in truth) for n, d in decisions.items() if d != "uncertain"):
        return "incorrect"
    return "abstained" if "uncertain" in decisions.values() else "correct"


def _ratio(n, d):
    return round(n / d, 6) if d else None


def _metrics(c):
    tp, fp, tn, fn, pa, na = (c[k] for k in ("tp", "fp", "tn", "fn", "positive_abstain", "negative_abstain"))
    return {"true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn,
            "positive_abstentions": pa, "negative_abstentions": na,
            "positive_support": tp + fn + pa, "negative_support": tn + fp + na,
            "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn + pa),
            "f1": _ratio(2 * tp, 2 * tp + fp + fn + pa),
            "accuracy_all": _ratio(tp + tn, tp + fp + tn + fn + pa + na),
            "decision_coverage": _ratio(tp + fp + tn + fn, tp + fp + tn + fn + pa + na)}


def _count(decision, positive):
    if decision == "uncertain":
        return "positive_abstain" if positive else "negative_abstain"
    if positive:
        return "tp" if decision == "present" else "fn"
    return "fp" if decision == "present" else "tn"


def _summary(rows, names):
    transitions, correctness = Counter(), Counter()
    baseline, candidate = defaultdict(Counter), defaultdict(Counter)
    changes, before_ambiguous, after_ambiguous, before_unclassified, after_unclassified = 0, 0, 0, 0, 0
    for old, new, truth in rows:
        transitions[old[0], new[0]] += 1
        changes += old != new
        before_ambiguous += old[0] == "ambiguous"
        after_ambiguous += new[0] == "ambiguous"
        before_unclassified += old[0] == "unclassified"
        after_unclassified += new[0] == "unclassified"
        a, b = _decisions(old, names), _decisions(new, names)
        correctness[_state(a, truth), _state(b, truth)] += 1
        for n in names:
            baseline[n][_count(a[n], n in truth)] += 1
            candidate[n][_count(b[n], n in truth)] += 1
    count = len(rows)
    old_correct = sum(v for (a, _), v in correctness.items() if a == "correct")
    new_correct = sum(v for (_, b), v in correctness.items() if b == "correct")
    gains = sum(v for (a, b), v in correctness.items() if a != "correct" and b == "correct")
    losses = sum(v for (a, b), v in correctness.items() if a == "correct" and b != "correct")
    return {"messages": count, "changed_decisions": changes,
            "baseline_ambiguous": before_ambiguous, "candidate_ambiguous": after_ambiguous,
            "baseline_unclassified": before_unclassified, "candidate_unclassified": after_unclassified,
            "baseline_strict_accuracy": _ratio(old_correct, count),
            "candidate_strict_accuracy": _ratio(new_correct, count),
            "strict_accuracy_delta": _ratio(new_correct - old_correct, count),
            "newly_correct": gains, "newly_not_correct": losses,
            "primary_transitions": [{"baseline": a, "candidate": b, "messages": v}
                                    for (a, b), v in sorted(transitions.items())],
            "correctness_transitions": [{"baseline": a, "candidate": b, "messages": correctness[a, b]}
                                        for a in ("correct", "incorrect", "abstained")
                                        for b in ("correct", "incorrect", "abstained")],
            "per_intent": [{"intent": n, "baseline": _metrics(baseline[n]),
                            "candidate": _metrics(candidate[n])} for n in names]}


def evaluate(current, candidate, examples, settings):
    before, after, names, chosen, pins = snapshot(current, candidate, examples, settings)
    rows, languages = [], defaultdict(list)
    for example in chosen:
        row = (_outcome(example["text"], before), _outcome(example["text"], after), set(example["labels"]))
        rows.append(row)
        languages[example["language"]].append(row)
    # Preserve exact all-population totals; bound language output.
    displayed = sorted(languages, key=lambda name: (-len(languages[name]), name))[:30]
    old = {i.name: set(i.phrases) for i in before}
    new = {i.name: set(i.phrases) for i in after}
    return {"format": FORMAT, **pins, "selected_split": "train",
            "labelled_training_messages": len(chosen),
            "heldout_text_used": False, "taxonomy_changed": False,
            "local_only": True, "external_validation": False,
            "term_changes": [{"intent": n, "added_normalized_terms": len(new[n] - old[n]),
                              "removed_normalized_terms": len(old[n] - new[n])} for n in names],
            "overall": _summary(rows, names),
            "language_groups_total": len(languages), "language_groups_displayed": len(displayed),
            "by_language": [{"language": name, **_summary(languages[name], names)} for name in displayed],
            "interpretation": "Retrospective labelled training examples only. Changes may overfit this population; "
                              "no holdout inference, model training, relabelling or automatic approval was performed."}
