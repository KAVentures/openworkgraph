"""Explicit local preparation of a paired aggregate/case company handoff.

No Gateway request, access grant, upload, source write or published artifact is
created by preparing this document. The existing case exporter computes both
representations under the workspace lock.
"""
from __future__ import annotations

from shared.analysis_handoff_bundle import HandoffBundle
from .analysis_case_export import prepare as prepare_pair


def prepare(log_id: str, spec: dict) -> dict:
    pair = prepare_pair(log_id, spec)
    return HandoffBundle(aggregate_snapshot=pair["aggregate_snapshot"],
                         case_dataset=pair["case_dataset"]).model_dump(mode="json")


__all__ = ["prepare"]
