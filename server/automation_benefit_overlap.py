"""Bounded, read-only cross-idea review of overlapping realized-value evidence (R16).

Two measurements describing the same named data source and overlapping *after*
periods can count the same work twice in a portfolio claim. They might also
describe different case populations: this is a review candidate, not proof.
The report never sums benefits, verifies source identity or infers causality.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date
import json
from typing import Any

from . import automation_benefits as benefits
from . import automation_pipeline as pipeline

FORMAT = "openworkgraph.automation-benefit-overlap-review.v1"
MAX_ROWS = pipeline.MAX_IDEAS * benefits.MAX_MEASUREMENTS_PER_IDEA
MAX_CANDIDATES = 100


def _empty() -> dict[str, Any]:
    return {
        "format": FORMAT, "status": "no_exact_overlap_detected",
        "implemented_ideas_with_after_measurements": 0,
        "after_measurements": 0, "unlinked_after_measurements": 0,
        "unreadable_measurements": 0, "rows_truncated": False,
        "candidate_pairs": [], "candidate_pairs_truncated": False,
        "source_reference_values_returned": False,
        "source_identity_verified": False, "same_cases_verified": False,
        "causal_effect_verified": False, "realized_savings_verified": False,
        "execution_authorized": False,
        "scope": ("Implemented local ideas, after-period measurements with exactly matching "
                  "nonempty declared source kind/reference, overlapping inclusive dates."),
        "interpretation": (
            "Exact source references and overlapping dates suggest measurements worth reviewing. "
            "They do not establish that the same cases were counted twice. Unlinked or different "
            "references can also describe overlapping cases, so no candidate is not proof of "
            "independent benefit. No monetary or effort savings are summed."
        ),
    }


def _parse(rows: list[tuple[str, str, str]]) -> tuple[list[dict], int, int, int, set[str]]:
    linked, unlinked, unreadable, after, ideas = [], 0, 0, 0, set()
    for idea_id, measurement_id, raw in rows:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("non-object measurement")
            if data.get("period") != "after":
                continue
            after += 1
            ideas.add(idea_id)
            start, end = date.fromisoformat(data["start"]), date.fromisoformat(data["end"])
            if end < start:
                raise ValueError("reversed dates")
            kind, ref = data.get("source"), data.get("source_reference")
            if not isinstance(kind, str) or not kind or not isinstance(ref, str) or not ref.strip():
                unlinked += 1
                continue
            # Use the exact, typed user-declared identity only; never merge by title,
            # time, source labels that are missing, or similarity of work.
            linked.append({"idea_id": idea_id, "measurement_id": measurement_id,
                           "source_key": (kind, ref.strip()), "start": start, "end": end})
        except (ValueError, TypeError, KeyError, OverflowError):
            unreadable += 1
    return linked, after, unlinked, unreadable, ideas


def _candidates(linked: list[dict]) -> tuple[list[dict], bool]:
    by_source: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in linked:
        by_source[row["source_key"]].append(row)
    result = []
    # Source labels are only used as an in-memory equality key. Output includes
    # a temporary group number, not a potentially sensitive provider reference.
    for number, (source_key, rows) in enumerate(sorted(by_source.items()), 1):
        rows.sort(key=lambda r: (r["start"], r["end"], r["idea_id"], r["measurement_id"]))
        for i, first in enumerate(rows):
            for other in rows[i + 1:]:
                if other["start"] > first["end"]:
                    break
                if other["idea_id"] == first["idea_id"]:
                    continue  # Already covered by per-idea benefit checks.
                if len(result) == MAX_CANDIDATES:
                    return result, True
                result.append({
                    "source_group": f"source_{number}",
                    "source_kind": source_key[0],
                    "first_idea_id": first["idea_id"],
                    "first_measurement_id": first["measurement_id"],
                    "second_idea_id": other["idea_id"],
                    "second_measurement_id": other["measurement_id"],
                    "overlap_start": max(first["start"], other["start"]).isoformat(),
                    "overlap_end": min(first["end"], other["end"]).isoformat(),
                    "evidence_basis": "exact_declared_reference_and_date_overlap",
                    "same_cases_verified": False,
                })
    return result, False


def review() -> dict[str, Any]:
    """Read a single local SQLite snapshot, with a bounded count and no writes."""
    result = _empty()
    with pipeline._LOCK, benefits._LOCK, closing(pipeline._connect()) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='idea_measurements'"
        ).fetchone()
        if not exists:
            return result
        rows = conn.execute(
            """SELECT m.idea_id, m.measurement_id, m.body
               FROM idea_measurements AS m
               INNER JOIN ideas AS i ON i.idea_id = m.idea_id
               WHERE i.state = 'implemented'
               ORDER BY m.idea_id, m.measurement_id LIMIT ?""",
            (MAX_ROWS + 1,),
        ).fetchall()
    truncated = len(rows) > MAX_ROWS
    linked, after, unlinked, unreadable, ideas = _parse(rows[:MAX_ROWS])
    pairs, pair_limit = _candidates(linked)
    status = ("review_candidates" if pairs else "incomplete_evidence"
              if truncated or unreadable or unlinked else "no_exact_overlap_detected")
    return {**result, "status": status, "implemented_ideas_with_after_measurements": len(ideas),
            "after_measurements": after, "unlinked_after_measurements": unlinked,
            "unreadable_measurements": unreadable, "rows_truncated": truncated,
            "candidate_pairs": pairs, "candidate_pairs_truncated": pair_limit,
            "comparison_complete": not (truncated or unreadable or pair_limit)}


__all__ = ["review", "FORMAT", "MAX_ROWS", "MAX_CANDIDATES"]
