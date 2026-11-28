"""Local reviewed keyword change preview and exact compare-and-save boundary."""
from __future__ import annotations

import hmac
import re

from shared import communications_taxonomy_change as change
from shared import communications_mining as mining
from . import communications_review as review


def _snapshot(load_current, candidate):
    data = review._load()
    review._require_enabled(data)
    return load_current(), data["examples"], data["settings"]


def _pins(load_current, candidate):
    current, examples, settings = _snapshot(load_current, candidate)
    return change.snapshot(current, candidate, examples, settings)[-1]


def preview(load_current, candidate):
    with review._LOCK:
        current, examples, settings = _snapshot(load_current, candidate)
    report = change.evaluate(current, candidate, examples, settings)
    with review._LOCK:
        latest = _pins(load_current, candidate)
        if latest["preview_sha256"] != report["preview_sha256"]:
            raise review.ReviewError("training examples or taxonomy changed during preview; preview again")
    return report


def apply(load_current, save_current, candidate, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise mining.CommunicationsError("an exact preview digest is required")
    with review._LOCK:
        pins = _pins(load_current, candidate)
        if not hmac.compare_digest(expected, pins["preview_sha256"]):
            raise review.ReviewError("training examples or taxonomy changed; preview again")
        # Ordinary taxonomy writes and reset use the same local lock. No review
        # labels, holdout rows, model or external permissions are modified.
        save_current(candidate)
        return {"taxonomy": candidate, "is_default": False,
                "applied_preview_sha256": expected,
                "taxonomy_sha256": pins["candidate_taxonomy_sha256"]}
