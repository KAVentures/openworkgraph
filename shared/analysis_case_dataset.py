"""Explicit bounded case disclosures: codes and within-case elapsed time, never anonymization."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator
from .analysis_snapshot import Strict, canonical, content_digest

FORMAT = 'openworkgraph.analysis-case-dataset.v1'
MAX_CASES = 500
MAX_EVENTS = 5000
MAX_BYTES = 512 * 1024
MAX_ELAPSED_US = 3155760000000000  # 100 years; exactly representable in a browser integer.
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class Event(Strict):
    activity: Annotated[str, Field(pattern=r'^A[1-9][0-9]{0,2}$')]
    elapsed_microseconds: Annotated[StrictInt, Field(ge=0, le=MAX_ELAPSED_US)]


class Case(Strict):
    code: Annotated[str, Field(pattern=r'^C[1-9][0-9]{0,2}$')]
    events: list[Event] = Field(min_length=1, max_length=MAX_EVENTS)


class CaseDataset(Strict):
    format: Literal['openworkgraph.analysis-case-dataset.v1'] = FORMAT
    generated_at: datetime
    source_content_sha256: Digest
    analysis_spec_sha256: Digest
    fingerprint_algorithm: Literal['openworkgraph.discovery-source.v1'] = 'openworkgraph.discovery-source.v1'
    cases: list[Case] = Field(min_length=1, max_length=MAX_CASES)
    original_labels_included: Literal[False] = False
    absolute_times_included: Literal[False] = False
    source_independently_verified: Literal[False] = False
    execution_authorized: Literal[False] = False

    @model_validator(mode='after')
    def complete_bounded_cases(self):
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError('generation time needs a timezone')
        seen = set()
        events = 0
        for i, case in enumerate(self.cases, 1):
            if case.code != f'C{i}':
                raise ValueError('case codes must be consecutive within this file')
            previous = 0
            if case.events[0].elapsed_microseconds != 0:
                raise ValueError('each case must start at elapsed time zero')
            for event in case.events:
                if event.elapsed_microseconds < previous:
                    raise ValueError('case events must be in elapsed-time order')
                previous = event.elapsed_microseconds
                if event.activity not in seen:
                    if event.activity != f'A{len(seen)+1}' or len(seen) >= 100:
                        raise ValueError('activity codes must follow their first occurrence, at most 100')
                    seen.add(event.activity)
                events += 1
        if events > MAX_EVENTS:
            raise ValueError('case dataset exceeds 5000 events; no partial cases are shared')
        if len(canonical(self.model_dump(mode='json'))) > MAX_BYTES:
            raise ValueError('case dataset exceeds 512 KiB')
        return self
