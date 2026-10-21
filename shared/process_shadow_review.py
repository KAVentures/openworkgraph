"""Validate minimized customer shadow reports against exact saved process specs.

Reports remain customer assertions. Inspection grants no execution permission,
verifies no business ground truth and performs no writes or provider calls.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from .process_agent_project import build_runtime_spec

Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Count = Annotated[StrictInt, Field(ge=0, le=50)]
Reason = Literal['matches_reviewed_expectation', 'invalid_or_stale_plan', 'invalid_route',
    'unresolved_review_bypassed', 'proposal_has_unresolved_inputs', 'expected_clarification',
    'unexpected_route', 'clarification_without_question', 'unknown_activity_in_clarification',
    'unexpected_clarification']


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ProcessReference(_StrictModel):
    workflow_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    revision: StrictInt = Field(ge=1)
    content_sha256: Hash


class ShadowCaseResult(_StrictModel):
    case_id: str = Field(pattern=r'^case_[0-9]{1,4}$')
    category: Literal['normal', 'exception', 'missing_facts']
    case_sha256: Hash
    matches_reviewed_expectation: StrictBool
    reason: Reason
    review_required: StrictBool

    @model_validator(mode='after')
    def consistent_status(self):
        matches = self.reason == 'matches_reviewed_expectation'
        if self.matches_reviewed_expectation is not matches or self.review_required is matches:
            raise ValueError('Case status must agree with its reason')
        return self


class ShadowEvaluation(_StrictModel):
    format: Literal['openworkgraph.shadow-evaluation.v1']
    process_reference: ProcessReference
    spec_sha256: Hash
    suite_sha256: Hash
    results_sha256: Hash
    evaluated_at: str = Field(min_length=1, max_length=64)
    case_count: StrictInt = Field(ge=1, le=50)
    matching_case_count: Count
    reason_counts: dict[Reason, Annotated[StrictInt, Field(ge=1, le=50)]]
    cases: list[ShadowCaseResult] = Field(min_length=1, max_length=50)
    expectations_are_customer_declarations: StrictBool
    independent_business_ground_truth_verified: StrictBool
    representative_population_coverage_established: StrictBool
    business_rules_evaluated: StrictBool
    case_inputs_and_model_text_included: StrictBool
    business_tools_called: StrictBool
    execution_authorized: StrictBool
    source_outcomes_verified: StrictBool
    process_model_updated: StrictBool
    review_required: StrictBool

    @field_validator('evaluated_at')
    @classmethod
    def aware_time(cls, value):
        if datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('Report time must include an offset')
        return value

    @model_validator(mode='after')
    def minimized_non_authorizing_report(self):
        false_flags = ('independent_business_ground_truth_verified', 'representative_population_coverage_established',
            'business_rules_evaluated', 'case_inputs_and_model_text_included', 'business_tools_called',
            'execution_authorized', 'source_outcomes_verified', 'process_model_updated')
        if self.expectations_are_customer_declarations is not True or any(getattr(self, key) is not False for key in false_flags):
            raise ValueError('Only minimized non-authorizing customer shadow reports can be inspected')
        reasons = Counter(row.reason for row in self.cases)
        matching = sum(row.matches_reviewed_expectation for row in self.cases)
        if (len(self.cases) != self.case_count or len({row.case_id for row in self.cases}) != self.case_count
                or self.matching_case_count != matching or self.reason_counts != dict(reasons)
                or self.review_required is not any(row.review_required for row in self.cases)):
            raise ValueError('Report totals, unique cases and review status must agree')
        return self


class ShadowReviewRequest(_StrictModel):
    workflow_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    revision: StrictInt = Field(ge=1)
    report: ShadowEvaluation


def inspect_shadow_report(packet: dict, report: ShadowEvaluation, *, latest_revision: int) -> dict:
    spec = build_runtime_spec(packet)
    expected = {key: spec[key] for key in ('workflow_id', 'revision', 'content_sha256')}
    spec_hash = hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if report.process_reference.model_dump() != expected or report.spec_sha256 != spec_hash:
        raise ValueError('The report does not identify this exact exported specification')
    return {'format': 'openworkgraph.process-shadow-review.v1', 'process_reference': expected,
        'exact_specification_match': True, 'latest_revision_at_inspection': latest_revision,
        'references_latest_revision_at_inspection': latest_revision == spec['revision'],
        'report': report.model_dump(), 'report_origin': 'customer_supplied_unverified_evaluation',
        'report_authenticity_verified': False, 'customer_expectations_independently_verified': False,
        'source_outcomes_verified': False, 'execution_authorized': False,
        'result_persisted': False, 'process_model_updated': False}
