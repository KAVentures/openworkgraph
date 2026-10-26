"""Human opportunity decisions over exact process/scenario revisions. No execution."""
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from .process_hierarchy import Strict, Reference, ID, Digest, Hierarchy
from .analysis_snapshot import content_digest
from .process_benefits import Currency, modeled_totals

MAX_REVISIONS=50
State=Literal['idea','assessing','approved','rejected','on_hold','implemented']
TRANSITIONS={'idea':{'assessing','rejected','on_hold'},'assessing':{'approved','rejected','on_hold'},
             'approved':{'implemented','on_hold'},'on_hold':{'assessing','rejected'},'rejected':{'idea'},'implemented':set()}


class Ease(Strict):
    rules_based: Annotated[StrictInt,Field(ge=1,le=5)]
    structured_inputs: Annotated[StrictInt,Field(ge=1,le=5)]
    process_stability: Annotated[StrictInt,Field(ge=1,le=5)]
    digital_inputs: Annotated[StrictInt,Field(ge=1,le=5)]
    low_exception_rate: Annotated[StrictInt,Field(ge=1,le=5)]


class Assessment(Strict):
    ease: Ease
    budget_currency: Currency
    implementation_budget: Annotated[str,Field(strict=True,pattern=r'^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$')]
    compliance_review_required: StrictBool=False

    @field_validator('implementation_budget')
    @classmethod
    def bounded_budget(cls,value):
        if Decimal(value)>1000000000:raise ValueError('Implementation budget must not exceed one billion')
        return format(Decimal(value).quantize(Decimal('0.01')),'f')


class Opportunity(Strict):
    process: Reference
    title: Annotated[str,Field(min_length=1,max_length=120)]
    description: Annotated[str,Field(max_length=2000)]=''
    coordinator_label: Annotated[str,Field(max_length=120)]=''
    team_label: Annotated[str,Field(max_length=120)]=''
    assessment: Assessment|None=None

    @field_validator('title','coordinator_label','team_label')
    @classmethod
    def single_line(cls,value):return Hierarchy.plain_text(value)

    @field_validator('description')
    @classmethod
    def valid_text(cls,value):value.encode('utf-8');return value

    @model_validator(mode='after')
    def title_required(self):
        if not self.title:raise ValueError('Opportunity title must not be blank')
        return self


def assessment_digest(data):
    return content_digest(data.model_dump(mode='json')) if data.assessment is not None else None


class Approval(Strict):
    actor_id: str
    assessment_sha256: Digest
    assessed_in_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    at: str
    compliance_review_declared_complete: StrictBool=False
    compliance_evidence_verified: Literal[False]=False
    execution_authorized: Literal[False]=False
    spending_authorized: Literal[False]=False


class Decision(Strict):
    actor_id: str
    target: State
    at: str
    note: Annotated[str,Field(max_length=1000)]=''
    implementation_self_report: StrictBool=False

    @field_validator('note')
    @classmethod
    def valid_text(cls,value):value.encode('utf-8');return value


class Saved(Strict):
    format: Literal['openworkgraph.shared-opportunity.v1']='openworkgraph.shared-opportunity.v1'
    organization_id: str
    data: Opportunity
    state: State
    assessment_author_actor_id: str|None=None
    assessed_in_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]|None=None
    assessment_sha256: Digest|None=None
    approval: Approval|None=None
    last_decision: Decision|None=None
    author_actor_id: str
    saved_at: str
    implementation_outcome_verified: Literal[False]=False
    execution_authorized: Literal[False]=False
    spending_authorized: Literal[False]=False

    @model_validator(mode='after')
    def exact_basis(self):
        digest=assessment_digest(self.data)
        if self.assessment_sha256!=digest:raise ValueError('Assessment digest differs from the declared snapshot')
        if (digest is None)!=(self.assessed_in_revision is None) or (digest is None)!=(self.assessment_author_actor_id is None):raise ValueError('Assessment author and revision must match its presence')
        if self.approval is not None and (self.approval.assessment_sha256!=digest or self.approval.assessed_in_revision!=self.assessed_in_revision or self.approval.actor_id==self.assessment_author_actor_id):raise ValueError('Approval must independently identify the exact assessment')
        if self.approval is not None and self.state not in {'approved','implemented'}:raise ValueError('Only approved stages may retain current approval')
        if self.last_decision is not None and self.last_decision.target!=self.state:raise ValueError('The last decision must identify the recorded stage')
        if self.approval is not None and self.data.assessment.compliance_review_required and not self.approval.compliance_review_declared_complete:raise ValueError('The declared compliance-review condition must be acknowledged')
        if self.state in {'approved','implemented'} and self.approval is None:raise ValueError('Approved stages require an exact independent decision')
        if self.state=='approved' and (self.last_decision is None or self.last_decision.actor_id!=self.approval.actor_id):raise ValueError('Approval must match its named stage decision')
        if self.state=='implemented' and (self.last_decision is None or not self.last_decision.implementation_self_report):raise ValueError('Implementation requires an explicit human self-report')
        return self


class Save(Strict):
    data: Opportunity
    expected_revision: Annotated[StrictInt,Field(ge=0,le=MAX_REVISIONS)]=0
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    user_confirmed: StrictBool=False
    organization_sharing_confirmed: StrictBool=False


class Transition(Strict):
    target: State
    expected_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    expected_assessment_sha256: Digest|None=None
    note: Annotated[str,Field(max_length=1000)]=''
    compliance_review_declared_complete: StrictBool=False
    implementation_self_report_confirmed: StrictBool=False
    user_confirmed: StrictBool=False

    @field_validator('note')
    @classmethod
    def valid_text(cls,value):value.encode('utf-8');return value


class Remove(Strict):
    expected_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    user_confirmed: StrictBool=False


def score(data,scenario):
    if data.assessment is None or scenario is None:return None
    ease=sum(v-1 for v in data.assessment.ease.model_dump().values())*5
    projection=modeled_totals([scenario]);hours=Decimal(projection['assumed_hours_saved_per_month'])
    # The threshold uses raw normalized assumptions, not the rounded display.
    with localcontext() as context:
        context.prec=60
        raw=Decimal(str(scenario['monthly_cases']))*Decimal(str(scenario['eligible_fraction_percent']))/100*Decimal(str(scenario['minutes_saved_per_case']))/60
    quadrant={(True,True):'quick_win',(True,False):'fill_in',(False,True):'major_project',(False,False):'deprioritize'}[(ease>=60,raw>=20)]
    return {'ease_score':ease,'modeled_hours_per_month':format(hours,'f'),'quadrant':quadrant,
        'thresholds':{'ease':60,'modeled_hours_per_month':'20.00'},'projection':projection,
        'effort_measured':False,'realized_savings_verified':False,'execution_authorized':False}
