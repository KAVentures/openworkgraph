"""One reviewable, source-pinned aggregate + case handoff to the company portal.

The bundle remains a local download until an authorized human explicitly
publishes the aggregate and separately approves the case attachment.
"""
from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .analysis_snapshot import Snapshot, Strict, canonical
from .analysis_case_dataset import CaseDataset

FORMAT = "openworkgraph.analysis-handoff-bundle.v1"
MAX_BYTES = 640 * 1024


class HandoffBundle(Strict):
    format: Literal["openworkgraph.analysis-handoff-bundle.v1"] = FORMAT
    aggregate_snapshot: Snapshot
    case_dataset: CaseDataset
    source_independently_verified: Literal[False] = False
    case_attachment_authorized: Literal[False] = False
    organization_publication_authorized: Literal[False] = False

    @model_validator(mode="after")
    def paired_and_bounded(self):
        aggregate, cases = self.aggregate_snapshot, self.case_dataset
        if aggregate.level != "cases":
            raise ValueError("paired handoff requires a case-level aggregate")
        if (aggregate.source_content_sha256 != cases.source_content_sha256 or
                aggregate.analysis_spec_sha256 != cases.analysis_spec_sha256):
            raise ValueError("aggregate and case dataset must share exact source and analysis fingerprints")
        if aggregate.matching_cases != len(cases.cases):
            raise ValueError("case dataset must represent the full selected aggregate case count")
        if len(canonical(self.model_dump(mode="json"))) > MAX_BYTES:
            raise ValueError("paired handoff exceeds its 640 KiB size limit")
        return self


__all__ = ["HandoffBundle", "FORMAT", "MAX_BYTES"]
