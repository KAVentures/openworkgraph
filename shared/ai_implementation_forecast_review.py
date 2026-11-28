"""Compare a reviewed forecast to the existing retained quality-benefit result.

The caller recomputes the quality result from the local inventory, never trusts
an uploaded result. Scope comparability and all economics remain declarations.
"""
from fractions import Fraction
import math

from . import ai_implementation_planning as planning
from . import ai_implementation_portfolio_link as link
from . import ai_implementation_review as contract

FORMAT = 'openworkgraph.ai-implementation-forecast-review.v1'
QUALITY = 'openworkgraph.quality-adjusted-benefit-scenario.v1'
BASES = ('workload', 'eligible_share', 'net_human_effort', 'incremental_nonlabor_cost',
         'capacity_valuation', 'implementation_cost')
CLAIMS = ('measurement_sources_independently_verified', 'causal_effect_verified', 'savings_claimed',
          'financial_return_verified', 'business_outcomes_verified', 'execution_authorized')


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Invalid reported numeric value')
    return Fraction(str(value))


def _range(value):
    return None if value is None else (Fraction(value['low']), Fraction(value['high']))


def _comparison(name, bounds, reported, comparable, basis, *, currency_ok=True):
    if not currency_ok:
        status = 'currency_mismatch'
    elif not comparable:
        status = 'comparability_not_confirmed'
    elif bounds is None or reported is None:
        status = 'unknown'
    else:
        status = ('below_forecast_range' if reported < bounds[0] else
                  'above_forecast_range' if reported > bounds[1] else 'within_forecast_range')
    return {'metric': name, 'forecast_range': planning._show(bounds),
            'reported_value': None if reported is None else str(reported),
            'status': status, 'basis': basis}


def compare(args, ideas, reconciliation_pin, item_index, quality, expected_scenario_pin, comparability):
    reconciliation = link.reconcile(*args, ideas)
    link.require(reconciliation['reconciliation_sha256'] == reconciliation_pin, 'Portfolio snapshot changed')
    link.require(type(item_index) is int and 0 <= item_index < reconciliation['item_count'], 'Invalid item index')
    mapped = reconciliation['items'][item_index]
    saved = mapped['saved_idea']
    link.require(mapped['mapping_status'] == 'matched' and mapped['scenario_assessed'] and saved is not None
                 and saved['state'] == 'implemented', 'Select a matched implemented and assessed candidate')
    link.require(isinstance(comparability, dict) and set(comparability) == set(BASES) | {'basis_note'}
                 and all(type(comparability[k]) is bool for k in BASES)
                 and isinstance(comparability['basis_note'], str) and 0 < len(comparability['basis_note'].strip()) <= 2000
                 and len(comparability['basis_note']) <= 2000, 'Declare scope comparability and its reviewed basis')
    link.require(isinstance(quality, dict) and quality.get('format') == QUALITY
                 and quality.get('idea_id') == saved['idea_id'] and quality.get('idea_revision') == saved['revision']
                 and isinstance(expected_scenario_pin, str) and link.HEX.fullmatch(expected_scenario_pin)
                 and quality.get('scenario_sha256') == expected_scenario_pin
                 and isinstance(quality.get('snapshot_sha256'), str) and link.HEX.fullmatch(quality['snapshot_sha256'])
                 and all(quality.get(k) is False for k in CLAIMS), 'Quality result is stale, mismatched or overclaims verification')
    selected, excluded = quality['selected_measurement_ids'], quality['excluded_measurement_ids']
    link.require(isinstance(selected, list) and len(selected) >= 2 and isinstance(excluded, list)
                 and len(selected) + len(excluded) <= 48 and len(set(selected + excluded)) == len(selected) + len(excluded),
                 'Invalid measurement population')
    scenario = planning.compare(*args[:-1])['candidates'][item_index]['assessment']
    ranges = scenario['declared_assessment']['ranges']
    bounds = {k: planning._interval(v, k) for k, v in ranges.items()}
    financial, after, spec = quality['financial_scenario'], quality['after'], quality['assumptions']
    currency_ok = quality['currency'] == reconciliation['currency']
    link.require(spec['currency'] == quality['currency'] and spec['snapshot_sha256'] == quality['snapshot_sha256'],
                 'Quality assumptions mismatch')
    link.require(type(after['cases']) is int and after['cases'] > 0 and type(after['eligible']) is int
                 and 0 <= after['eligible'] <= after['cases'], 'Invalid after denominator')
    v = lambda k: _number(financial[k]) if financial[k] is not None else None
    cost_delta = v('nonlabor_cost_change_at_after_volume')
    entries = [
        _comparison('monthly_case_volume', bounds['cases_per_month'], v('after_monthly_case_volume'),
                    comparability['workload'], 'Existing engine after-volume normalization (30.44-day month)'),
        _comparison('eligible_share', bounds['eligible_share'], Fraction(after['eligible'], after['cases']),
                    comparability['eligible_share'], 'Reviewer-declared eligible cases / retained after cases'),
        _comparison('monthly_net_human_hours', _range(scenario['modeled_net_human_hours_per_month']),
                    v('effort_change_with_extra_review_hours_at_after_volume'),
                    comparability['workload'] and comparability['eligible_share'] and comparability['net_human_effort'],
                    'Retained before/after human effort including declared extra review, at after volume; not causal'),
        _comparison('monthly_incremental_nonlabor_cost', bounds['incremental_operating_cost_per_month'],
                    None if cost_delta is None else -cost_delta,
                    comparability['workload'] and comparability['incremental_nonlabor_cost'],
                    'Negative of existing baseline-minus-after nonlabor cost change, normalized to after volume', currency_ok=currency_ok),
        _comparison('capacity_value_per_hour', bounds['human_capacity_value_per_hour'], _number(spec['labor_cost_per_hour']),
                    comparability['capacity_valuation'], 'Reviewer-entered valuation assumption, not measured cash savings', currency_ok=currency_ok),
        _comparison('one_time_implementation_cost', bounds['one_time_implementation_cost'], _number(spec['one_time_cost']),
                    comparability['implementation_cost'], 'Reviewer-entered upfront cost, not independently verified actual spend', currency_ok=currency_ok),
        _comparison('monthly_conditional_net_value', _range(scenario['modeled_capacity_value_less_operating_cost_per_month']),
                    v('modeled_net_monthly_value_change'),
                    all(comparability[k] for k in BASES if k != 'implementation_cost'),
                    'Existing conditional value calculation; not verified financial return', currency_ok=currency_ok),
    ]
    body = {'format': FORMAT, 'packet_sha256': args[1], 'planning_review_sha256': args[-1],
            'reconciliation_sha256': reconciliation_pin, 'item_index': item_index,
            'proposal_sha256': mapped['proposal_sha256'], 'idea_id': saved['idea_id'], 'idea_revision': saved['revision'],
            'measurement_snapshot_sha256': quality['snapshot_sha256'], 'quality_scenario_sha256': expected_scenario_pin,
            'quality_result_sha256': contract._digest(quality), 'selected_measurement_ids': selected,
            'excluded_measurement_ids': excluded, 'forecast_currency': reconciliation['currency'],
            'measurement_scenario_currency': quality['currency'], 'comparability': comparability, 'comparisons': entries,
            'review_gates': quality['review_gates'], 'all_declared_gates_met': quality['all_declared_gates_met'],
            'whole_population_quality_threshold_status': quality['whole_population_quality']['threshold_status'],
            'forecast_recorded_before_implementation_verified': False, 'comparability_independently_verified': False,
            'portfolio_written': False, **{k: False for k in CLAIMS},
            'interpretation': 'Compared reported rounded values from the existing quality-benefit engine to reviewed forecast ranges. Boundary comparisons can be sensitive to display precision. Scope and currency checks are not quality or causal verification. Retained periods may be selectively chosen; excluded IDs remain visible. No prospective timing, realized savings or deployment conclusion is established.'}
    return {**body, 'forecast_review_sha256': contract._digest(body)}
