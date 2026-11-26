"""Transient dashboard review of externally pinned workforce audit artifacts.

Do not consult or mutate the local trust registry. Historical reviewers must
supply independently obtained trust anchors, not embedded packet keys.
"""
from __future__ import annotations

from pydantic import Field, StrictBool

from shared.agent_procurement import Strict, MAX_BYTES, digest
from shared.process_shadow_review import Hash
from . import agent_procurement_workforce_dossier as audit
from . import agent_procurement_workforce_timeline as timeline


class VerifyRequest(Strict):
    confirmed: StrictBool
    expected_dossier_sha256: Hash
    trust: dict
    dossier: dict


class TimelineRequest(Strict):
    confirmed: StrictBool
    expected_manifest_sha256: Hash
    manifest: dict
    dossiers: list[dict] = Field(min_length=2, max_length=timeline.MAX_SNAPSHOTS)


def _bounded(document):
    # The HTTP route also checks the original UTF-8 JSON text byte length.
    # This second boundary covers direct callers and canonical expansion.
    if len(audit._canonical(document)) > MAX_BYTES:
        raise ValueError("use the offline CLI for larger audit artifacts")


def verify(document):
    _bounded(document)
    req = VerifyRequest.model_validate(document)
    if not req.confirmed:
        raise ValueError("explicit historical evidence review confirmation required")
    # Use the caller's exact JSON objects, not a coerced model dump.
    receipt = audit.verify(document["dossier"], document["trust"],
                           expected_dossier_sha256=req.expected_dossier_sha256)
    body = {"format": "openworkgraph.workforce-audit-dashboard-review.v1",
            "verification": receipt, "report": document["dossier"]["report"],
            "result_persisted": False, "local_trust_registry_used": False,
            "execution_authorized": False}
    return {**body, "review_sha256": digest(body)}


def review_timeline(document):
    _bounded(document)
    req = TimelineRequest.model_validate(document)
    if not req.confirmed:
        raise ValueError("explicit historical evidence review confirmation required")
    result = timeline.review(document["dossiers"], document["manifest"],
                             expected_manifest_sha256=req.expected_manifest_sha256)
    body = {"format": "openworkgraph.workforce-audit-dashboard-timeline.v1",
            "timeline": result, "result_persisted": False,
            "local_trust_registry_used": False, "execution_authorized": False}
    return {**body, "review_sha256": digest(body)}
