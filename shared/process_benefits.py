"""Explicit scenario contribution declarations; modeled value is never realized value."""
from decimal import Decimal, localcontext, ROUND_HALF_UP
from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .process_hierarchy import Strict, Reference, ID, Hierarchy, Remove
from .analysis_snapshot import content_digest
from .process_opportunities import OpportunityScenario

MAX_LINKS = 20
MAX_PLANS = 100
MAX_REVISIONS = 20
Currency = Literal['USD','EUR','GBP','SEK']


class Contribution(Strict):
    id: ID
    scenario_process: Reference
    include_in_estimate: StrictBool = False
    benefit_scope_id: ID


class BenefitPlan(Strict):
    format: Literal['openworkgraph.process-benefit-plan.v1'] = 'openworkgraph.process-benefit-plan.v1'
    organization_id: Annotated[str,Field(min_length=1,max_length=512)]
    root_process: Reference
    title: Annotated[str,Field(min_length=1,max_length=120)]
    currency: Currency
    links: list[Contribution] = Field(default_factory=list,max_length=MAX_LINKS)
    disjoint_scopes_reviewed: StrictBool = False
    scope_basis: Literal['human_declared_not_independently_verified'] = 'human_declared_not_independently_verified'
    realized_savings_verified: Literal[False] = False
    execution_authorized: Literal[False] = False

    @model_validator(mode='after')
    def bounded_declaration(self):
        # Reuse repository metadata's Unicode/single-line boundary.
        self.title=Hierarchy.plain_text(self.title)
        if not self.title:raise ValueError('A nonblank benefit plan title is required')
        if len({r.id for r in self.links})!=len(self.links) or len({r.scenario_process.workflow_id for r in self.links})!=len(self.links):
            raise ValueError('Each scenario process may appear only once in a contribution plan')
        return self


class Saved(Strict):
    format: Literal['openworkgraph.saved-process-benefit-plan.v1'] = 'openworkgraph.saved-process-benefit-plan.v1'
    plan: BenefitPlan
    author_actor_id: str
    saved_at: str


class Save(Strict):
    plan: BenefitPlan
    expected_revision: Annotated[StrictInt,Field(ge=0,le=MAX_REVISIONS)] = 0
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    user_confirmed: StrictBool = False
    organization_sharing_confirmed: StrictBool = False


class PortfolioReview(Strict):
    currency: Currency
    expected_portfolio_sha256: Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    user_confirmed: StrictBool = False
    disjoint_scopes_confirmed: StrictBool = False


def modeled_totals(scenarios):
    """Sum raw decimal assumptions once; no rounded child sums or float totals."""
    with localcontext() as context:
        context.prec=60
        hours=gross=running=one_time=sensitivity=Decimal(0)
        currencies=set()
        for value in scenarios:
            s=OpportunityScenario.model_validate(value);currencies.add(s.currency)
            d=lambda name:Decimal(str(getattr(s,name)))
            effort=d('monthly_cases')*d('eligible_fraction_percent')/100*d('minutes_saved_per_case')/60
            value=effort*d('loaded_hourly_cost')
            hours+=effort;gross+=value;running+=d('recurring_monthly_cost');one_time+=d('one_time_implementation_cost')
            sensitivity+=value*d('sensitivity_percent')/100
        if not currencies or len(currencies)!=1:raise ValueError('A nonempty single-currency scenario set is required')
        def text(value):return format(value.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP),'f')
        net=gross-running
        return {'currency':next(iter(currencies)),'assumed_hours_saved_per_month':text(hours),
            'gross_labor_value_per_month':text(gross),'recurring_monthly_cost':text(running),
            'net_monthly_value_before_other_costs':text(net),'one_time_implementation_cost':text(one_time),
            'sensitivity_net_monthly_low':text(net-sensitivity),'sensitivity_net_monthly_high':text(net+sensitivity),
            'simple_payback_months':text(one_time/net) if net>0 else None,
            'numeric_representation':'decimal_strings_rounded_once_half_up',
            'basis':'human_declared_disjoint_scenarios_not_measured_or_verified_savings',
            'sensitivity_is_statistical_confidence_interval':False,'overlap_independently_verified':False,
            'realized_savings_verified':False,'execution_authorized':False}
