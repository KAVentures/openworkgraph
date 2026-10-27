"""Human implementation baselines and separately reported costs; no spending grants."""
from datetime import date
from decimal import Decimal,localcontext
from typing import Annotated,Literal
from pydantic import Field,StrictBool,StrictInt,field_validator,model_validator
from .process_hierarchy import Strict,Reference,ID,Digest,Hierarchy
from .process_benefits import Currency
from .process_decision_tables import validate_date
from .analysis_snapshot import content_digest

MAX_PHASES=12
MAX_REVISIONS=50
Money=Annotated[str,Field(strict=True,pattern=r'^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$')]
Hours=Annotated[str,Field(strict=True,pattern=r'^(0|[1-9][0-9]{0,6})(\.[0-9]{1,2})?$')]


def amount(value,maximum):
    if value is None:return None
    if Decimal(value)>maximum:raise ValueError('Declared amount exceeds the planning bound')
    return format(Decimal(value).quantize(Decimal('0.01')),'f')


class Phase(Strict):
    id: ID
    name: Annotated[str,Field(min_length=1,max_length=120)]
    cost: Money
    effort_hours: Hours
    starts_on: str
    finishes_on: str

    @field_validator('name')
    @classmethod
    def text(cls,value):
        value=Hierarchy.plain_text(value)
        if not value:raise ValueError('Phase name required')
        return value

    @field_validator('cost')
    @classmethod
    def cost_bound(cls,value):return amount(value,1000000000)
    @field_validator('effort_hours')
    @classmethod
    def effort_bound(cls,value):return amount(value,1000000)
    @field_validator('starts_on','finishes_on')
    @classmethod
    def dates(cls,value):return validate_date(value)
    @model_validator(mode='after')
    def dates_ordered(self):
        if self.finishes_on<self.starts_on:raise ValueError('Phase finish must not precede its start')
        return self


class Baseline(Strict):
    title: Annotated[str,Field(min_length=1,max_length=120)]
    opportunity: Reference
    currency: Currency
    phases: list[Phase]=Field(min_length=1,max_length=MAX_PHASES)

    @field_validator('title')
    @classmethod
    def text(cls,value):return Phase.text(value)
    @model_validator(mode='after')
    def unique_phases(self):
        if len({p.id for p in self.phases})!=len(self.phases):raise ValueError('Phase IDs must be unique')
        return self


class PhaseReport(Strict):
    phase_id: ID
    status: Literal['planned','in_progress','on_hold','completed','cancelled']='planned'
    cost_to_date: Money|None=None
    effort_hours_to_date: Hours|None=None
    remaining_cost_estimate: Money|None=None
    remaining_effort_hours_estimate: Hours|None=None
    started_on: str|None=None
    finished_on: str|None=None
    completion_self_report_confirmed: StrictBool=False
    actual_values_independently_verified: Literal[False]=False
    business_outcome_verified: Literal[False]=False

    @field_validator('cost_to_date','remaining_cost_estimate')
    @classmethod
    def cost_bound(cls,value):return amount(value,1000000000)
    @field_validator('effort_hours_to_date','remaining_effort_hours_estimate')
    @classmethod
    def effort_bound(cls,value):return amount(value,1000000)
    @field_validator('started_on','finished_on')
    @classmethod
    def dates(cls,value):return validate_date(value) if value is not None else None
    @model_validator(mode='after')
    def consistency(self):
        if self.started_on and self.finished_on and self.finished_on<self.started_on:raise ValueError('Reported finish must not precede the reported start')
        if self.status in {'completed','cancelled'}:
            if not self.completion_self_report_confirmed or self.finished_on is None:raise ValueError('Explicitly confirm the phase-completion self-report and date')
            if self.status=='completed' and self.started_on is None:raise ValueError('Completed phase needs its declared start date')
            if self.cost_to_date is None or self.effort_hours_to_date is None or self.remaining_cost_estimate!='0.00' or self.remaining_effort_hours_estimate!='0.00':raise ValueError('Completed/cancelled reports require explicit actual amounts and zero remaining estimates')
        elif self.finished_on is not None:raise ValueError('Only a completed/cancelled report has a finished date')
        return self


class Review(Strict):
    actor_id: str
    at: str
    baseline_sha256: Digest
    assessed_in_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    approved_opportunity: Reference
    execution_authorized: Literal[False]=False
    spending_authorized: Literal[False]=False


class ReportAttribution(Strict):
    phase_id: ID
    actor_id: str
    at: str
    reported_in_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]


class Saved(Strict):
    format: Literal['openworkgraph.implementation-phase-plan.v1']='openworkgraph.implementation-phase-plan.v1'
    organization_id: str
    baseline: Baseline
    baseline_author_actor_id: str
    assessed_in_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    baseline_sha256: Digest
    review: Review|None=None
    reports: list[PhaseReport]=Field(default_factory=list,max_length=MAX_PHASES)
    report_attributions: list[ReportAttribution]=Field(default_factory=list,max_length=MAX_PHASES)
    author_actor_id: str
    saved_at: str
    execution_authorized: Literal[False]=False
    spending_authorized: Literal[False]=False
    actual_values_independently_verified: Literal[False]=False
    realized_savings_verified: Literal[False]=False
    benefit_rollup_included: Literal[False]=False

    @model_validator(mode='after')
    def exact_baseline(self):
        if self.baseline_sha256!=content_digest(self.baseline.model_dump(mode='json')):raise ValueError('Baseline digest differs from its exact snapshot')
        if self.review is not None and (self.review.baseline_sha256!=self.baseline_sha256 or self.review.assessed_in_revision!=self.assessed_in_revision or self.review.actor_id==self.baseline_author_actor_id or self.review.approved_opportunity.workflow_id!=self.baseline.opportunity.workflow_id):raise ValueError('Baseline review must independently bind this exact phase baseline')
        ids={p.id for p in self.baseline.phases}
        if len({r.phase_id for r in self.reports})!=len(self.reports) or any(r.phase_id not in ids for r in self.reports):raise ValueError('Reports require unique existing phase IDs')
        if len({r.phase_id for r in self.report_attributions})!=len(self.report_attributions) or {r.phase_id for r in self.report_attributions}!={r.phase_id for r in self.reports}:raise ValueError('Each report needs its exact server attribution')
        return self


class SaveBaseline(Strict):
    baseline: Baseline
    expected_revision: Annotated[StrictInt,Field(ge=0,le=MAX_REVISIONS)]=0
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    user_confirmed: StrictBool=False
    organization_sharing_confirmed: StrictBool=False


class SaveReports(Strict):
    reports: list[PhaseReport]=Field(min_length=1,max_length=MAX_PHASES)
    expected_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    expected_baseline_sha256: Digest
    user_confirmed: StrictBool=False
    organization_sharing_confirmed: StrictBool=False

    @model_validator(mode='after')
    def unique(self):
        if len({r.phase_id for r in self.reports})!=len(self.reports):raise ValueError('Each reported phase must be unique')
        return self


class Freeze(Strict):
    expected_approved_opportunity: Reference
    expected_revision: Annotated[StrictInt,Field(ge=1,le=MAX_REVISIONS)]
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    expected_baseline_sha256: Digest
    user_confirmed: StrictBool=False


class Remove(Strict):
    expected_revision: Annotated[StrictInt,Field(ge=1,le=2147483647)]
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    user_confirmed: StrictBool=False


def totals(baseline,reports):
    """Exact decimal amounts, explicit unknown coverage, no inferred effort/value."""
    indexed={r['phase_id']:r for r in reports};phases=baseline['phases'];count=len(phases)
    with localcontext() as context:
        context.prec=60
        fmt=lambda value:format(value.quantize(Decimal('0.01')),'f')
        cost=sum((Decimal(p['cost']) for p in phases),Decimal(0));hours=sum((Decimal(p['effort_hours']) for p in phases),Decimal(0))
        result={'currency':baseline['currency'],'baseline_cost':fmt(cost),'baseline_effort_hours':fmt(hours),
            'baseline_calendar_span_days':(date.fromisoformat(max(p['finishes_on'] for p in phases))-date.fromisoformat(min(p['starts_on'] for p in phases))).days+1,
            'phase_count':count,'coverage':{},'actual_values_independently_verified':False,'realized_savings_verified':False,'spending_authorized':False,'execution_authorized':False}
        for key in ('cost_to_date','effort_hours_to_date','remaining_cost_estimate','remaining_effort_hours_estimate'):
            values=[indexed.get(p['id'],{}).get(key) for p in phases];known=[v for v in values if v is not None]
            result[key]=fmt(sum((Decimal(v) for v in known),Decimal(0))) if len(known)==count else None
            result['coverage'][key]={'known_phases':len(known),'unknown_phases':count-len(known)}
        for suffix,actual,remaining,base in [('cost','cost_to_date','remaining_cost_estimate',cost),('effort_hours','effort_hours_to_date','remaining_effort_hours_estimate',hours)]:
            new=Decimal(result[actual])+Decimal(result[remaining]) if result[actual] is not None and result[remaining] is not None else None
            result['new_total_'+suffix+'_estimate']=fmt(new) if new is not None else None
            result['variance_'+suffix+'_from_baseline']=fmt(new-base) if new is not None else None
    return result
