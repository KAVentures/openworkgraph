"""Similar activity labels: names that probably stand for the same activity, as mapping suggestions.

Exports from several systems, or from one system over time, often spell the same step in different
ways: "Approve invoice", "approve  invoice", "Approve Invoice.", or a typo. Each spelling becomes
its own activity, which splits the map and the variants. Process mining tools offer label
clean-up for this. This module finds candidate groups among the log's original activity names:

* **same apart from case, spacing or punctuation**: names that are equal after case-folding,
  Unicode normalization, removing punctuation and collapsing whitespace;
* **similar spelling**: among the most common names, pairs whose normalized forms are at least
  ``SIMILARITY`` alike (difflib ratio), ignoring pairs that differ in their digits ("Step 1" and
  "Step 2" are different steps).

Each group comes with a suggested label (its most frequent spelling). Nothing is changed: the groups
can be turned into an activity mapping, which the owner reviews and applies. A suggestion is a
guess from spelling, not knowledge that two activities are the same.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event_log_label_suggestions.v1"
SIMILARITY = 0.9
MAX_COMPARED = 400
MIN_LENGTH = 5
MAX_GROUPS = 100
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")


def _normal(label: str) -> str:
    text = unicodedata.normalize("NFKC", label).casefold()
    text = _PUNCT.sub(" ", text).replace("_", " ")
    return _SPACE.sub(" ", text).strip()


def suggestions(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = conn.execute("SELECT activity, count(*) FROM events WHERE log_id = ? GROUP BY activity "
                                "ORDER BY count(*) DESC, activity LIMIT 5000", [log_id]).fetchall()
        finally:
            conn.close()
    events = dict(rows)
    by_key: dict[str, list[str]] = {}
    for label, _ in rows:
        by_key.setdefault(_normal(label), []).append(label)
    # Union-find over normalized keys, so a label ends up in at most one group.
    parent = {k: k for k in by_key}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    keys = sorted(by_key, key=lambda k: -sum(events[a] for a in by_key[k]))[:MAX_COMPARED]
    for i, a in enumerate(keys):
        if len(a) < MIN_LENGTH:
            continue
        for b in keys[i + 1:]:
            if len(b) < MIN_LENGTH or abs(len(a) - len(b)) > max(len(a), len(b)) * (1 - SIMILARITY) + 1:
                continue
            if _DIGITS.findall(a) != _DIGITS.findall(b):
                continue
            if SequenceMatcher(None, a, b).ratio() >= SIMILARITY:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    clusters: dict[str, list[str]] = {}
    for k in by_key:
        clusters.setdefault(find(k), []).extend(by_key[k])
    groups = []
    for labels in clusters.values():
        if len(labels) < 2:
            continue
        labels = sorted(labels, key=lambda a: (-events[a], a))
        similar = ("similar spelling" if len({_normal(a) for a in labels}) > 1
                   else "same apart from case, spacing or punctuation")
        groups.append({"suggested_label": labels[0], "reason": similar,
                       "labels": [{"activity": a, "events": events[a]} for a in labels],
                       "events": sum(events[a] for a in labels)})
    groups.sort(key=lambda g: (-g["events"], g["suggested_label"]))
    return {
        "format": FORMAT, "log_id": log_id, "activities": len(rows),
        "groups": groups[:MAX_GROUPS], "groups_total": len(groups),
        "method": ("names equal after case-folding, Unicode normalization and removing punctuation and extra spaces, "
                   f"or (among the {MAX_COMPARED} most common) at least {SIMILARITY:.0%} alike with the same digits"),
        "caveat": ("A suggestion is a guess from spelling. Check each group before turning it into an activity mapping; "
                   "similar names can be different steps."),
    }


__all__ = ["suggestions", "FORMAT"]
