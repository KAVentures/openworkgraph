"""Exact phased investment scenarios: cash flow and capacity value are distinct.

All money and fractions originate in bounded finite JSON numbers. Fractions
preserve sign, discounting and threshold comparisons before display rounding.
No forecast, verified savings, accounting advice or authority is inferred.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

FORMAT = "openworkgraph.phased-investment-business-case.v1"
MAX_MONTHS = 60
MAX_PHASES = 12
MAX_DESIGNS = 4
MAX_PERIODS = 48
NUMBER = Annotated[int | float, Field(ge=0, le=10**12)]
SIGNED = Annotated[int | float, Field(ge=-10**12, le=10**12)]
SHARE = Annotated[int | float, Field(ge=0, le=1)]
MONTH = Annotated[int, Field(ge=1, le=MAX_MONTHS)]
MEASUREMENT_ID = Annotated[str, Field(pattern=r"^meas_[a-f0-9]{16}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Phase(Strict):
    start_month: MONTH
    end_month: MONTH
    monthly_cases: Annotated[int, Field(ge=0, le=100_000_000)]
    retained_effort_effect_share: SHARE
    nonlabor_cash_change_per_case: SIGNED
    recurring_fixed_cash_cost: NUMBER
    one_time_cash_cost_at_phase_start: NUMBER


class Design(Strict):
    label: str = Field(min_length=1, max_length=80)
    cash_conversion_share: SHARE
    initial_cash_cost: NUMBER
    phases: list[Phase] = Field(min_length=1, max_length=MAX_PHASES)


class Scenario(Strict):
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    measurement_ids: list[MEASUREMENT_ID] = Field(min_length=2, max_length=MAX_PERIODS)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    labor_hour_value: NUMBER
    monthly_discount_rate: Annotated[int | float, Field(ge=0, le=1)]
    horizon_months: MONTH
    designs: list[Design] = Field(min_length=1, max_length=MAX_DESIGNS)
    conversion_sensitivity: list[SHARE] = Field(min_length=2, max_length=11)


def q(value):
    return Fraction(str(value))


def digest(value):
    return sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,
                            separators=(",",":"),allow_nan=False).encode()).hexdigest()


def display(value, digits=6):
    """Fixed decimal string for all financial and ratio outputs."""
    if value is None:
        return None
    with localcontext() as ctx:
        ctx.prec=100
        return format(Decimal(value.numerator)/Decimal(value.denominator),f".{digits}f")


def validate(spec):
    if len(set(spec.measurement_ids))!=len(spec.measurement_ids):
        raise ValueError("select distinct retained measurement IDs")
    labels=set()
    for design in spec.designs:
        name=design.label.strip().casefold()
        if not name or not design.label.isprintable() or design.label.strip()!=design.label or name in labels:
            raise ValueError("designs require distinct printable names")
        labels.add(name)
        expected=1
        for p in design.phases:
            if p.start_month!=expected or p.end_month<p.start_month:
                raise ValueError("phases must be ordered, contiguous, positive and nonoverlapping")
            expected=p.end_month+1
        if expected!=spec.horizon_months+1:
            raise ValueError("each design must explicitly cover every month of the common horizon")
    if len(set(q(x) for x in spec.conversion_sensitivity))!=len(spec.conversion_sensitivity):
        raise ValueError("conversion sensitivity shares must be distinct")


def _break_even(cumulative):
    # First crossing and first crossing with no later negative month are
    # different when ramp-up or replacement costs can create a reversal.
    first=next((i for i,x in enumerate(cumulative) if x>=0),None)
    last_negative=max((i for i,x in enumerate(cumulative) if x<0),default=-1)
    sustained=last_negative+1 if last_negative+1<len(cumulative) else None
    return {"first_nonnegative_month":first,"sustained_nonnegative_from_month":sustained,
            "returns_below_zero_after_first_crossing":first is not None and
                any(x<0 for x in cumulative[first+1:]),
            "within_declared_horizon_only":True}


def _sensitivity(base_npv, release_npv, shares):
    if release_npv<0:
        raise AssertionError("positive release value must be nonnegative")
    threshold=None
    if base_npv>=0:
        status="nonnegative_without_cash_conversion"
        threshold=Fraction(0)
    elif release_npv==0:
        status="not_attainable_without_positive_release"
    elif base_npv+release_npv<0:
        status="not_attainable_even_at_full_conversion"
    else:
        status="attainable_under_declared_assumptions"
        threshold=-base_npv/release_npv
    conservative=None
    if threshold is not None:
        with localcontext() as ctx:
            ctx.prec=100
            # Integer ceiling on the exact rational, not on a rounded quotient.
            scaled=-((-threshold.numerator*10**10)//threshold.denominator)
            conservative=format(Decimal(scaled)/Decimal(10**10),".10f")
    return {"status":status,"minimum_cash_conversion_share_for_nonnegative_npv":conservative,
            "minimum_share_display_rounded_up":True,
            "sensitivity":[{"cash_conversion_share":display(s,10),"cash_npv":display(base_npv+s*release_npv),
                            "cash_npv_nonnegative":base_npv+s*release_npv>=0} for s in sorted(shares)],
            "cash_npv_at_zero_conversion":display(base_npv),
            "cash_npv_at_full_conversion":display(base_npv+release_npv),
            "signs_evaluated_before_display_rounding":True}


def _scenario(design,spec,effort_delta):
    hour_value=q(spec.labor_hour_value)
    conversion=q(design.cash_conversion_share)
    discount=1+q(spec.monthly_discount_rate)
    initial=q(design.initial_cash_cost)
    cash=economic=-initial
    cash_pv=economic_pv=-initial
    cash_path=[cash]; discounted_path=[cash_pv]
    economic_path=[economic]; economic_discounted_path=[economic_pv]
    base_pv=-initial; release_pv=Fraction(0)
    total_released=total_extra=total_unconverted=Fraction(0)
    totals={k:Fraction(0) for k in ("converted_positive_labor_value","additional_labor_cash_cost",
        "nonlabor_cash_change","fixed_cash_cost","phase_cash_cost","unconverted_capacity_value")}
    rows=[{"month":0,"phase":None,"cases":0,"released_hours":"0.000000","additional_hours":"0.000000",
           "converted_positive_labor_value":"0.000000","unconverted_capacity_value":"0.000000",
           "additional_labor_cash_cost":"0.000000","nonlabor_cash_change":"0.000000",
           "fixed_cash_cost":"0.000000","one_time_cash_cost":display(initial),
           "net_cash_flow":display(-initial),"net_economic_value":display(-initial),
           "discounted_cash_flow":display(-initial),"discounted_economic_value":display(-initial),
           "cumulative_cash_flow":display(cash),"cumulative_discounted_cash_flow":display(cash_pv),
           "cumulative_economic_value":display(economic),"cumulative_discounted_economic_value":display(economic_pv)}]
    for phase_number,p in enumerate(design.phases,1):
        for month in range(p.start_month,p.end_month+1):
            # Effort delta is reported baseline minus after hours per case.
            # A deterioration is not converted into a positive capacity benefit.
            delta=effort_delta*p.monthly_cases*q(p.retained_effort_effect_share)
            released=max(delta,Fraction(0)); extra=max(-delta,Fraction(0))
            value=released*hour_value
            converted=value*conversion
            unconverted=value-converted
            additional_cost=extra*hour_value
            nonlabor=q(p.nonlabor_cash_change_per_case)*p.monthly_cases
            fixed=q(p.recurring_fixed_cash_cost)
            phase_cost=q(p.one_time_cash_cost_at_phase_start) if month==p.start_month else Fraction(0)
            base=nonlabor-fixed-phase_cost-additional_cost
            net_cash=base+converted
            net_economic=base+value
            factor=discount**month
            cash+=net_cash;economic+=net_economic
            cash_pv+=net_cash/factor;economic_pv+=net_economic/factor
            base_pv+=base/factor;release_pv+=value/factor
            total_released+=released;total_extra+=extra;total_unconverted+=released*(1-conversion)
            for k,v in (("converted_positive_labor_value",converted),("additional_labor_cash_cost",additional_cost),
                        ("nonlabor_cash_change",nonlabor),("fixed_cash_cost",fixed),("phase_cash_cost",phase_cost),
                        ("unconverted_capacity_value",unconverted)):
                totals[k]+=v
            cash_path.append(cash);discounted_path.append(cash_pv)
            economic_path.append(economic);economic_discounted_path.append(economic_pv)
            rows.append({"month":month,"phase":phase_number,"cases":p.monthly_cases,
                "released_hours":display(released),"additional_hours":display(extra),
                "converted_positive_labor_value":display(converted),"unconverted_capacity_value":display(unconverted),
                "additional_labor_cash_cost":display(additional_cost),"nonlabor_cash_change":display(nonlabor),
                "fixed_cash_cost":display(fixed),"one_time_cash_cost":display(phase_cost),
                "net_cash_flow":display(net_cash),"net_economic_value":display(net_economic),
                "discounted_cash_flow":display(net_cash/factor),"discounted_economic_value":display(net_economic/factor),
                "cumulative_cash_flow":display(cash),"cumulative_discounted_cash_flow":display(cash_pv),
                "cumulative_economic_value":display(economic),"cumulative_discounted_economic_value":display(economic_pv)})
    if cash_pv!=base_pv+conversion*release_pv or economic_pv!=base_pv+release_pv:
        raise AssertionError("discounted decomposition failed")
    metrics={"cash_npv":cash_pv,"economic_npv":economic_pv,"undiscounted_cash":cash,
             "undiscounted_economic_value":economic,"released_hours":total_released}
    report={"label":design.label,"cash_conversion_share":display(conversion,10),
            "initial_cash_cost":display(initial),"horizon_months":spec.horizon_months,
            **{k:display(v) for k,v in metrics.items()},
            "additional_hours":display(total_extra),"unconverted_released_hours":display(total_unconverted),
            "cash_npv_nonnegative":cash_pv>=0,"economic_npv_nonnegative":economic_pv>=0,
            "peak_undiscounted_cash_funding_requirement":display(max(Fraction(0),-min(cash_path))),
            "peak_discounted_cash_funding_requirement":display(max(Fraction(0),-min(discounted_path))),
            "undiscounted_cash_break_even":_break_even(cash_path),
            "discounted_cash_break_even":_break_even(discounted_path),
            "undiscounted_economic_break_even":_break_even(economic_path),
            "discounted_economic_break_even":_break_even(economic_discounted_path),
            "totals":{k:display(v) for k,v in totals.items()},
            "cash_conversion_review":_sensitivity(base_pv,release_pv,
                {q(v) for v in spec.conversion_sensitivity}|{conversion,Fraction(0),Fraction(1)}),
            "months":rows}
    return report,metrics


def evaluate(spec,effort_delta):
    """Compare explicit alternative scenarios with one common effort basis."""
    validate(spec)
    result=[_scenario(design,spec,effort_delta) for design in spec.designs]
    baseline=result[0][1]
    designs=[]
    for report,metrics in result:
        report["change_vs_first_design"]={k:display(v-baseline[k]) for k,v in metrics.items()}
        designs.append(report)
    return {"format":FORMAT,"scenario_sha256":digest(spec.model_dump()),
            "assumptions":spec.model_dump(),"currency":spec.currency,
            "baseline_design":spec.designs[0].label,"designs":designs,
            "cash_conversion_applies_only_to_positive_released_capacity":True,
            "additional_labor_hours_costed_at_full_declared_hour_value":True,
            "cash_costs_end_of_month_except_initial_cost":True,
            "scenario_probabilities_assumed":False,"financial_return_verified":False,
            "actual_cash_savings_verified":False,"causal_effect_verified":False,
            "execution_authorized":False,
            "interpretation":"Conditional scenarios, not forecasts or booked savings. Released labor capacity has economic value only at the entered hourly valuation; only the declared converted share enters modeled cash flow. Additional labor is conservatively charged at the full declared hourly value. Nonlabor changes and all costs are explicit assumptions; no quality or adoption result is inferred. No design is automatically recommended."}
