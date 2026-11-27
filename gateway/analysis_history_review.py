"""Authorized, exact revision-history and declared target review for R4.

Only the existing publication store may resolve retained revisions. A preview
is a pin inventory, not permission to read later. Each review rechecks the
entire selected prefix, integrity, current ACL, expiry and expected digests.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from itertools import combinations
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .analysis_publications import PublicationError
from .analysis_revision_comparisons import compare_snapshots

FORMAT = "openworkgraph.analysis-history-review.v1"
MAX_REVISIONS = 10
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[StrictInt, Field(ge=1, le=MAX_REVISIONS)]
# Fixed-point string, bounded length, no float coercion, exponent or NaN.
Threshold = Annotated[str, Field(strict=True, max_length=65,
                               pattern=r"^-?(0|[1-9][0-9]{0,30})(\.[0-9]{1,30})?$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pin(Strict):
    revision: Revision
    expected_content_sha256: Digest


class Target(Strict):
    heading: Annotated[str, Field(pattern=r"^Measure [1-6]$")]
    operator: Literal["at_least", "at_most", "between"]
    threshold: Threshold
    upper: Threshold | None = None

    @model_validator(mode="after")
    def range_valid(self):
        if self.operator == "between":
            if self.upper is None or Decimal(self.upper) < Decimal(self.threshold):
                raise ValueError("Choose ordered inclusive range endpoints")
        elif self.upper is not None:
            raise ValueError("Upper endpoint is only for a range")
        return self


class HistoryRequest(Strict):
    pins: Annotated[list[Pin], Field(min_length=2, max_length=MAX_REVISIONS)]
    anchor_revision: Revision
    targets: Annotated[list[Target], Field(max_length=6)] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_prefix(self):
        if [p.revision for p in self.pins] != list(range(1, len(self.pins)+1)):
            raise ValueError("Pin every revision in order from revision one")
        if self.anchor_revision > len(self.pins):
            raise ValueError("Anchor must be part of the reviewed prefix")
        if len({t.heading for t in self.targets}) != len(self.targets):
            raise ValueError("Choose at most one target per measure")
        return self


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _basis(detail):
    doc = detail["snapshot"]
    return {
        "revision": detail["viewing_revision"], "title": detail["title"],
        "content_sha256": detail["content_sha256"],
        "published_at": detail["published_at"], "generated_at": doc["generated_at"],
        "source_content_sha256": doc["source_content_sha256"],
        "analysis_spec_sha256": doc["analysis_spec_sha256"],
        "matching_cases": doc["matching_cases"], "matching_rows": doc["matching_rows"],
    }


def preview(store, principal, view_id):
    """Read every retained revision through the same authoritative store."""
    with store.transaction(principal) as conn:
        meta = store.meta(conn, principal, view_id)
        details = [store.detail_in(conn, principal, meta, r)
                   for r in range(1, meta["revision"]+1)]
        rows = [{**_basis(d), "columns": d["snapshot"]["columns"]}
                for d in details]
        result = {
            "format": "openworkgraph.analysis-history-preview.v1",
            "view_id": view_id, "latest_revision": meta["revision"],
            "revisions": rows, "source_independently_verified": False,
        }
        store.audit(conn, principal, "analysis.history_previewed", view_id=view_id,
                    revisions=[{"revision": d["revision"], "sha256": d["content_sha256"]}
                               for d in rows])
        return result


def _comparisons(details):
    """Check all unordered pairs, withhold every conflicting basis member."""
    pairs, conflicts, blocked = {}, [], set()
    for i, j in combinations(range(len(details)), 2):
        pair = compare_snapshots(details[i], details[j])
        pairs[i, j] = pair
        if pair["refusal_reasons"] == ["same_fingerprint_but_different_values"]:
            conflicts.append([i+1, j+1])
            blocked.update((i, j))
    return pairs, conflicts, blocked


def _pair(details, i, j, blocked):
    p = compare_snapshots(details[i], details[j])
    reasons = list(p["refusal_reasons"])
    if i in blocked or j in blocked:
        reasons.append("history_conflicting_claimed_basis")
    return {
        "from_revision": i+1, "to_revision": j+1,
        "comparable": not reasons, "refusal_reasons": reasons,
        "same_claimed_source": p["same_claimed_source"],
        "changes": p["changes"] if not reasons else [],
    }


def _fraction_text(value):
    """Exact finite base-ten representation (no Decimal context rounding)."""
    sign = "-" if value < 0 else ""
    num, den = abs(value.numerator), value.denominator
    twos = fives = 0
    while den % 2 == 0:
        den //= 2
        twos += 1
    while den % 5 == 0:
        den //= 5
        fives += 1
    if den != 1:
        raise ValueError("Expected a finite decimal fraction")
    scale = max(twos, fives)
    digits = str(num * (2 ** (scale-twos)) * (5 ** (scale-fives)))
    if scale:
        digits = digits.zfill(scale+1)
        digits = digits[:-scale] + "." + digits[-scale:]
    return sign + digits


def _target_result(target, column_index, column, details, anchor_pairs, adjacent):
    low = Fraction(Decimal(target.threshold))
    high = Fraction(Decimal(target.upper)) if target.upper is not None else None
    rows = []
    for i, detail in enumerate(details):
        pair = anchor_pairs[i]
        if not pair["comparable"]:
            rows.append({"revision": i+1, "status": "withheld", "value": None,
                         "distance_to_target_decimal": None,
                         "refusal_reasons": pair["refusal_reasons"]})
            continue
        raw = detail["snapshot"]["rows"][0][column_index]
        value = Fraction(Decimal(str(raw)))
        if target.operator == "at_least":
            gap = max(Fraction(0), low-value)
        elif target.operator == "at_most":
            gap = max(Fraction(0), value-low)
        else:
            gap = max(Fraction(0), low-value, value-high)
        rows.append({"revision": i+1, "status": "outside" if gap else "met",
                     "value": raw, "distance_to_target_decimal": _fraction_text(gap),
                     "refusal_reasons": []})
    transitions = []
    for i, pair in enumerate(adjacent):
        before, after = rows[i]["status"], rows[i+1]["status"]
        if not pair["comparable"] or "withheld" in (before, after):
            status = "withheld"
        elif before == after:
            status = "remained_" + after
        else:
            status = "entered_target" if after == "met" else "left_target"
        transitions.append({"from_revision": i+1, "to_revision": i+2, "status": status})
    counts = Counter(r["status"] for r in rows)
    # A withheld revision breaks a streak: no interpolation or presumed pass.
    longest = current = 0
    for row in rows:
        current = current+1 if row["status"] == "met" else 0
        longest = max(longest, current)
    return {
        "target": target.model_dump(mode="json"), "column": column,
        "revision_results": rows, "transitions": transitions,
        "counts": {key: counts[key] for key in ("met", "outside", "withheld")},
        "longest_consecutive_met_revisions": longest,
    }


def review(store, principal, view_id, request: HistoryRequest):
    with store.transaction(principal) as conn:
        meta = store.meta(conn, principal, view_id)
        details = [store.detail_in(conn, principal, meta, p.revision) for p in request.pins]
        if any(d["content_sha256"] != p.expected_content_sha256
               for d, p in zip(details, request.pins, strict=True)):
            raise PublicationError("A retained revision differs from its reviewed digest; reopen history", 409)
        anchor = request.anchor_revision-1
        columns = details[anchor]["snapshot"]["columns"]
        by_heading = {c["heading"]: (i, c) for i, c in enumerate(columns)
                      if c["kind"] == "number"}
        if any(t.heading not in by_heading for t in request.targets):
            raise PublicationError("Choose target measures from the exact anchor revision", 422)

        _, conflicts, blocked = _comparisons(details)
        anchor_pairs = [_pair(details, anchor, i, blocked) for i in range(len(details))]
        adjacent = [_pair(details, i, i+1, blocked) for i in range(len(details)-1)]
        targets = [_target_result(t, *by_heading[t.heading], details, anchor_pairs, adjacent)
                   for t in request.targets]
        report = {
            "format": FORMAT, "view_id": view_id,
            "anchor_revision": request.anchor_revision,
            "reviewed_through_revision": len(details), "latest_revision_at_review": meta["revision"],
            "newer_revisions_excluded": meta["revision"]-len(details),
            "revisions": [_basis(d) for d in details],
            "anchor_comparisons": anchor_pairs,
            "adjacent_comparisons": adjacent,
            "conflicting_claimed_basis_pairs": conflicts,
            "targets": targets,
            "adjacent_comparable_count": sum(p["comparable"] for p in adjacent),
            "anchor_comparable_count": sum(p["comparable"] for p in anchor_pairs),
            "source_independently_verified": False, "case_identities_joined": False,
            "pooled_totals_reported": False, "causal_effect_estimated": False,
            "execution_authorized": False, "targets_are_user_declared": True,
            "interpretation": [
                "Revision order is publication order, not a calendar time series or proof of distinct populations.",
                "All revisions in the pinned prefix were checked. Newer revisions are not silently included.",
                "Every adjacent and anchor comparison passes the existing numeric compatibility rules. "
                "Every pair in the prefix is checked for contradictory claimed source and analysis basis.",
                "Targets use the exact anchor measure and inclusive thresholds. Withheld revisions have no "
                "target result; gaps and incompatible transitions are not interpolated.",
                "Values and exact decimal distances describe publisher-supplied snapshots. "
                "They do not prove source truth, adopted policy, causal effects, savings or execution permission.",
            ],
        }
        report["report_sha256"] = _digest(report)
        store.audit(conn, principal, "analysis.history_reviewed", view_id=view_id,
                    revisions=[{"revision": p.revision, "sha256": p.expected_content_sha256}
                               for p in request.pins],
                    anchor_revision=request.anchor_revision,
                    target_count=len(targets), conflicting_pair_count=len(conflicts),
                    adjacent_comparable_count=report["adjacent_comparable_count"],
                    report_sha256=report["report_sha256"])
        return report
