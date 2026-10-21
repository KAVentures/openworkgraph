"""Comparable, explicit-assumption scenarios; event gaps never become labor savings."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class OpportunityScenario(BaseModel):
    model_config = ConfigDict(extra='forbid')
    currency: Literal['SEK', 'USD', 'EUR', 'GBP']
    monthly_cases: Annotated[FiniteFloat, Field(strict=True, ge=1, le=10000000)]
    minutes_saved_per_case: Annotated[FiniteFloat, Field(strict=True, ge=0, le=10080)]
    eligible_fraction_percent: Annotated[FiniteFloat, Field(strict=True, ge=0, le=100)]
    loaded_hourly_cost: Annotated[FiniteFloat, Field(strict=True, ge=0, le=1000000)]
    recurring_monthly_cost: Annotated[FiniteFloat, Field(strict=True, ge=0, le=1000000000)]
    one_time_implementation_cost: Annotated[FiniteFloat, Field(strict=True, ge=0, le=1000000000)]
    sensitivity_percent: Annotated[FiniteFloat, Field(strict=True, ge=0, le=100)] = 25

    def projection(self) -> dict:
        eligible = self.monthly_cases * self.eligible_fraction_percent / 100
        hours = eligible * self.minutes_saved_per_case / 60
        gross = hours * self.loaded_hourly_cost
        net = gross - self.recurring_monthly_cost
        sensitivity = gross * self.sensitivity_percent / 100
        return {
            'currency': self.currency,
            'eligible_cases_per_month': round(eligible, 2),
            'assumed_hours_saved_per_month': round(hours, 2),
            'gross_labor_value_per_month': round(gross, 2),
            'net_monthly_value_before_other_costs': round(net, 2),
            'sensitivity_net_monthly_low': round(net - sensitivity, 2),
            'sensitivity_net_monthly_high': round(net + sensitivity, 2),
            'simple_payback_months': round(self.one_time_implementation_cost / net, 2) if net > 0 else None,
            'basis': 'user_supplied_assumptions_not_observed_labor_or_verified_savings',
            'sensitivity_is_statistical_confidence_interval': False,
            'guaranteed_savings': False,
        }
