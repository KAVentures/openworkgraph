"""Minimized, immutable numeric analysis publications; never executable evidence."""
from datetime import datetime
from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

FORMAT = 'openworkgraph.aggregate-analysis-snapshot.v1'
MAX_BYTES = 128 * 1024
Count = Annotated[StrictInt, Field(ge=0, le=10**12)]
Number = StrictInt | Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-(2**53-1), le=2**53-1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Column(Strict):
    heading: Annotated[str, Field(pattern=r'^(Group|Measure) [1-6]$')]
    kind: Literal['group', 'number']
    unit: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    aggregation: Literal['count','cases','share','median','p90','avg','min','max','sum'] | None = None
    value_basis: Literal['duration_seconds','events','distinct_activities','case_metric'] | None = None
    metric_slot: Annotated[StrictInt, Field(ge=1, le=4)] | None = None


class Coverage(Strict):
    slot: Annotated[StrictInt, Field(ge=1, le=4)]
    with_value: Count
    without_value: Count
    missing_operand: Count = 0
    zero_divisor: Count = 0
    non_finite: Count = 0


class Snapshot(Strict):
    format: Literal['openworkgraph.aggregate-analysis-snapshot.v1'] = FORMAT
    generated_at: datetime
    level: Literal['cases', 'events']
    columns: list[Column] = Field(min_length=1, max_length=6)
    rows: list[list[Number | Annotated[str, Field(pattern=r'^G[12]_[0-9]{1,3}$')] | None]] = Field(max_length=100)
    matching_cases: Count
    matching_rows: Count
    rows_truncated: StrictBool
    non_finite_results_withheld: Count = 0
    metric_coverage: list[Coverage] = Field(default_factory=list, max_length=4)
    source_content_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    analysis_spec_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    fingerprint_algorithm: Literal['openworkgraph.discovery-source.v1'] = 'openworkgraph.discovery-source.v1'
    group_labels_included: Literal[False] = False
    source_independently_verified: Literal[False] = False
    execution_authorized: Literal[False] = False

    @field_validator('rows', mode='before')
    @classmethod
    def safe_integers(cls, rows):
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, list) and any(type(v) is int and abs(v) > 2**53-1 for v in row):
                    raise ValueError('snapshot integers exceed exact browser representation')
        return rows

    @model_validator(mode='after')
    def bounded_table(self):
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError('snapshot time needs a timezone')
        if len({c.heading for c in self.columns}) != len(self.columns):
            raise ValueError('snapshot headings must be unique')
        groups = [c for c in self.columns if c.kind == 'group']
        if len(groups) > 2 or not any(c.kind == 'number' for c in self.columns):
            raise ValueError('snapshot needs numeric measures and at most two groups')
        for c in self.columns:
            if c.heading.startswith('Group') != (c.kind == 'group') or (c.kind == 'group' and c.unit is not None):
                raise ValueError('snapshot column kind and heading disagree')
            if c.kind == 'group' and any(v is not None for v in (c.aggregation,c.value_basis,c.metric_slot)):
                raise ValueError('group column cannot declare a measure')
            if c.kind == 'number' and (c.aggregation is None or (c.aggregation in {'count','cases','share'}) != (c.value_basis is None)):
                raise ValueError('numeric column needs its aggregate and value basis')
            if (c.value_basis == 'case_metric') != (c.metric_slot is not None):
                raise ValueError('case metric column needs its coverage slot')
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError('snapshot row width differs from headings')
            for c, value in zip(self.columns, row):
                if value is not None and ((c.kind == 'group') != isinstance(value, str)):
                    raise ValueError('snapshot cell type differs from its column')
        if len({c.slot for c in self.metric_coverage}) != len(self.metric_coverage):
            raise ValueError('coverage slots must be unique')
        if any(c.metric_slot is not None and c.metric_slot not in {v.slot for v in self.metric_coverage} for c in self.columns):
            raise ValueError('measure points to missing coverage')
        for c in self.metric_coverage:
            if c.with_value + c.without_value != self.matching_cases or any(
                n > c.without_value for n in (c.missing_operand, c.zero_divisor, c.non_finite)):
                raise ValueError('coverage differs from selected case count')
        if len(canonical(self.model_dump(mode='json'))) > MAX_BYTES:
            raise ValueError('snapshot exceeds 128 KiB; select fewer rows')
        return self


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def content_digest(value):
    return sha256(canonical(value)).hexdigest()
