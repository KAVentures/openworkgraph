"""Preview one explicit scenario-to-portfolio planning update; never approve work."""
from fractions import Fraction

from . import ai_implementation_planning as planning
from . import ai_implementation_portfolio_link as link
from . import ai_implementation_review as contract

FORMAT = 'openworkgraph.ai-implementation-portfolio-plan-preview.v1'
EDITABLE = {'idea', 'assessing', 'on_hold'}


def preview(args, ideas, reconciliation_pin, item_index, estimate):
    report = link.reconcile(*args, ideas)
    link.require(report['reconciliation_sha256'] == reconciliation_pin, 'Portfolio snapshot changed')
    link.require(type(item_index) is int and 0 <= item_index < report['item_count'], 'Invalid candidate index')
    row = report['items'][item_index]
    saved, deps = row['saved_idea'], row['dependency_comparison']
    link.require(row['mapping_status'] == 'matched' and saved is not None and saved['state'] in EDITABLE
                 and row['scenario_assessed'] and deps is not None, 'Select an editable matched assessed idea')
    scenario = planning.compare(*args[:-1])['candidates'][item_index]
    declared = scenario['assessment']['declared_assessment']
    link.require(declared['feasibility'] == 'plausible' and not scenario['dependencies_without_plausible_assessment']
                 and not deps['unresolved_candidate_indexes'], 'Resolve feasibility and dependencies first')
    by_id = {i['idea_id']: i for i in ideas}
    target = by_id[saved['idea_id']]
    required = deps['resolved_expected_idea_ids']
    link.require(all(by_id[d]['state'] != 'rejected' for d in required), 'A dependency is rejected')
    current = target.get('planning') or {}
    link.require(isinstance(current, dict) and set(current) <= {'owner','team','target_quarter','budget','depends_on'},
                 'Unrecognized existing planning fields')
    old = {'owner':current.get('owner',''), 'team':current.get('team',''),
           'target_quarter':current.get('target_quarter',''), 'budget':current.get('budget'),
           'depends_on':current.get('depends_on',[])}
    combined = sorted(set(old['depends_on']) | set(required))
    link.require(len(combined) <= 20, 'Too many saved dependencies')
    budget = old['budget']
    if estimate is not None:
        link.require(budget is None, 'Existing budget is preserved; use ordinary planning review to change it')
        interval = planning._interval([estimate, estimate], 'one_time_implementation_cost')
        bounds = planning._interval(declared['ranges']['one_time_implementation_cost'], 'one_time_implementation_cost')
        link.require(bounds is not None and bounds[0] <= interval[0] <= bounds[1], 'Estimate outside reviewed range')
        amount = float(interval[0])
        link.require(Fraction(str(amount)) == interval[0], 'Estimate cannot be represented exactly by existing budget field')
        budget = {'amount':amount, 'currency':report['currency'], 'basis':'estimate'}
    added = sorted(set(combined)-set(old['depends_on']))
    link.require(added or estimate is not None, 'No planning changes selected')
    payload = {**old, 'budget':budget, 'depends_on':combined, 'expected_revision':saved['revision']}
    body = {'format':FORMAT, 'reconciliation_sha256':reconciliation_pin, 'planning_review_sha256':args[-1],
            'item_index':item_index, 'idea_id':saved['idea_id'], 'idea_state':saved['state'],
            'previous_planning':old, 'proposed_planning':payload, 'added_dependency_idea_ids':added,
            'budget_estimate_added':estimate is not None, 'portfolio_written':False,
            'assessment_changed':False, 'approval_changed':False, 'execution_authorized':False,
            'interpretation':'One explicit planning update only. Existing unrelated links and budgets preserved. Scenario estimates are not approved budgets, measured savings, or execution authority.'}
    return {**body, 'preview_sha256':contract._digest(body)}
