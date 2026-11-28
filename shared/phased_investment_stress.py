"""R16 finite common-scenario stress review, using exact rational cash flows."""
from fractions import Fraction
from itertools import combinations

from . import phased_investment_business_case as base

FORMAT = "openworkgraph.investment-stress-review.v1"
FIELDS = {"label", "volume_factor", "effect_factor", "conversion_factor",
          "cost_factor", "nonlabor_factor", "discount_rate"}


def _number(x, lo, hi):
    if type(x) not in (int, float) or not lo <= x <= hi:
        raise ValueError("invalid bounded stress assumption")
    value = base.q(x)
    if (value * 10**8).denominator != 1:
        raise ValueError("stress factors require at most eight decimal places")
    return value


def normalize(rows, funding_limit):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
        raise ValueError("declare one to twelve common stress scenarios")
    labels = {"reference"}
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError("provide every common stress assumption explicitly")
        name = row["label"]
        if (not isinstance(name, str) or not 1 <= len(name) <= 80 or name.strip() != name
                or not name.isprintable() or name.casefold() in labels):
            raise ValueError("stress labels must be distinct printable names")
        labels.add(name.casefold())
        factors = {k: _number(row[k], 0, 3 if k in {"volume_factor", "cost_factor", "nonlabor_factor"} else 1)
                   for k in FIELDS - {"label"}}
        result.append({"label": name, **factors})
    limit = None if funding_limit is None else _number(funding_limit, 0, 10**18)
    return result, limit


def _cash(design, spec, delta, shock):
    q = base.q
    initial = q(design.initial_cash_cost) * shock["cost_factor"]
    cash = -initial
    economic = cash
    peak = initial
    pv = cash
    epv = cash
    path = [cash]
    discount = 1 + shock["discount_rate"]
    hourly = q(spec.labor_hour_value)
    for p in design.phases:
        for month in range(p.start_month, p.end_month + 1):
            # Fractional case volume is a planning expectation, not a counted case.
            cases = p.monthly_cases * shock["volume_factor"]
            hours = delta * cases * q(p.retained_effort_effect_share) * shock["effect_factor"]
            release = max(0, hours) * hourly
            extra = max(0, -hours) * hourly
            nonlabor = q(p.nonlabor_cash_change_per_case) * cases * shock["nonlabor_factor"]
            costs = q(p.recurring_fixed_cash_cost)
            if month == p.start_month:
                costs += q(p.one_time_cash_cost_at_phase_start)
            common = nonlabor - costs * shock["cost_factor"] - extra
            flow = common + release * q(design.cash_conversion_share) * shock["conversion_factor"]
            econ = common + release
            cash += flow
            economic += econ
            pv += flow / discount**month
            epv += econ / discount**month
            peak = max(peak, -cash)
            path.append(cash)
    return {"cash_npv": pv, "economic_npv": epv, "peak_funding": peak,
            "closing_cash": cash, "break_even": base._break_even(path)}


def _exact(value):
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > 12000:
        raise ValueError("exact stress output exceeds the supported rational size")
    return {"numerator": str(value.numerator), "denominator": str(value.denominator),
            "decimal": base.display(value)}


def review(spec, delta, shocks, funding_limit):
    base.validate(spec)
    declared, limit = normalize(shocks, funding_limit)
    _number(spec.monthly_discount_rate, 0, 1)
    reference = {"label": "Reference", "volume_factor": Fraction(1), "effect_factor": Fraction(1),
                 "conversion_factor": Fraction(1), "cost_factor": Fraction(1),
                 "nonlabor_factor": Fraction(1), "discount_rate": base.q(spec.monthly_discount_rate)}
    scenarios = [reference, *declared]
    labels = [d.label for d in spec.designs]
    values = {s["label"]: {d.label: _cash(d, spec, delta, s) for d in spec.designs} for s in scenarios}
    best = {k: max(x["cash_npv"] for x in v.values()) for k, v in values.items()}
    rows = []
    exact = {}
    for label in labels:
        cells = [(s["label"], values[s["label"]][label]) for s in scenarios]
        worst = min(v["cash_npv"] for _, v in cells)
        high = max(v["cash_npv"] for _, v in cells)
        max_funding = max(v["peak_funding"] for _, v in cells)
        regret = max(best[k] - v["cash_npv"] for k, v in cells)
        qualified = worst >= 0 and (limit is None or max_funding <= limit)
        exact[label] = (worst, regret, qualified)
        rows.append({"design": label, "worst_cash_npv": _exact(worst), "best_cash_npv": _exact(high),
                     "worst_cash_scenarios": [k for k, v in cells if v["cash_npv"] == worst],
                     "maximum_peak_funding": _exact(max_funding),
                     "peak_funding_scenarios": [k for k, v in cells if v["peak_funding"] == max_funding],
                     "maximum_cash_regret": _exact(regret),
                     "regret_scenarios": [k for k, v in cells if best[k] - v["cash_npv"] == regret],
                     "nonnegative_cash_npv_in_all_scenarios": worst >= 0,
                     "funding_limit_met_in_all_scenarios": None if limit is None else max_funding <= limit,
                     "passes_declared_gates": qualified,
                     "scenarios": [{"label": k, **{f: _exact(v[f]) for f in ("cash_npv", "economic_npv", "peak_funding", "closing_cash")},
                                    "cash_npv_nonnegative": v["cash_npv"] >= 0,
                                    "within_funding_limit": None if limit is None else v["peak_funding"] <= limit,
                                    "cash_regret": _exact(best[k] - v["cash_npv"]),
                                    "undiscounted_cash_break_even": v["break_even"]} for k, v in cells]})
    dominance = []
    for a, b in combinations(labels, 2):
        for left, right in ((a, b), (b, a)):
            differences = [(k, v[left]["cash_npv"] - v[right]["cash_npv"],
                            v[right]["peak_funding"] - v[left]["peak_funding"]) for k, v in values.items()]
            if all(c >= 0 and f >= 0 for _, c, f in differences) and any(c > 0 or f > 0 for _, c, f in differences):
                dominance.append({"dominant": left, "dominated": right,
                                  "minimum_cash_advantage": _exact(min(c for _, c, _ in differences)),
                                  "minimum_funding_advantage": _exact(min(f for _, _, f in differences))})
    qualified = [k for k in labels if exact[k][2]]
    def winners(index, fn):
        if not qualified:
            return []
        target = fn(exact[k][index] for k in qualified)
        return [k for k in qualified if exact[k][index] == target]
    return {"format": FORMAT, "currency": spec.currency, "reference_scenario_sha256": base.digest(spec.model_dump()),
            "reference_assumptions": spec.model_dump(), "exact_retained_effort_delta_hours_per_case": _exact(delta),
            "scenario_count": len(scenarios), "scenario_probabilities_assumed": False,
            "funding_limit": None if limit is None else _exact(limit),
            "scenarios": [{"label": s["label"], **{k: _exact(v) for k, v in s.items() if k != "label"}} for s in scenarios],
            "designs": rows, "robust_dominance": dominance, "passing_designs": qualified,
            "maximin_cash_npv_among_passing_designs": winners(0, max),
            "minimax_cash_regret_among_passing_designs": winners(1, min),
            "regret_reference_includes_all_tested_designs": True,
            "financial_return_verified": False, "actual_cash_savings_verified": False,
            "causal_effect_verified": False, "execution_authorized": False,
            "interpretation": "Finite declared common scenarios, not a probability model, forecast or worst case outside the listed assumptions. Regret compares each design with the highest cash NPV among all tested designs in that scenario, even if a comparator exceeds funding. Cash and economic capacity value are separate. Factors apply uniformly to each phase and design; nonlabor factor scales both positive and negative changes. No design is automatically recommended."}
