"""Read-only lineage reconciliation of a reviewed scenario and saved portfolio."""
from fractions import Fraction
import re

from . import ai_implementation_batch as batch
from . import ai_implementation_planning as planning
from . import ai_implementation_review as contract

FORMAT = 'openworkgraph.ai-implementation-portfolio-link.v1'
IDEA = re.compile(r'idea_[a-f0-9]{16}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')
STATES = {'idea', 'assessing', 'approved', 'rejected', 'on_hold', 'implemented'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def inventory(ideas):
    """Project only the metadata needed to match the exact retained proposal."""
    require(isinstance(ideas, list) and len(ideas) <= 1000, 'Invalid portfolio inventory')
    seen, out = set(), []
    for idea in ideas:
        require(isinstance(idea, dict), 'Invalid portfolio idea')
        iid, revision, state = (idea.get(k) for k in ('idea_id', 'revision', 'state'))
        require(isinstance(iid, str) and IDEA.fullmatch(iid) and iid not in seen
                and type(revision) is int and revision >= 1 and state in STATES,
                'Invalid or duplicate idea identity')
        seen.add(iid)
        lin = idea.get('ai_proposal_handoff')
        if lin is None:
            continue  # Manually typed source text is not trusted lineage.
        require(isinstance(lin, dict), 'Invalid retained lineage')
        p = idea.get('planning') or {}
        require(isinstance(p, dict), 'Invalid saved planning metadata')
        deps = p.get('depends_on', [])
        require(isinstance(deps, list) and len(deps) <= 20 and all(isinstance(d, str) and IDEA.fullmatch(d) for d in deps)
                and len(set(deps)) == len(deps), 'Invalid saved dependencies')
        out.append({'idea_id': iid, 'revision': revision, 'state': state,
                    'source': idea.get('source'), 'lineage': lin,
                    'budget': p.get('budget'), 'depends_on': sorted(deps),
                    'has_assessment': idea.get('assessment') is not None})
    return sorted(out, key=lambda x: x['idea_id'])


def _budget(saved, assessment, currency):
    if assessment is None:
        return 'scenario_unassessed'
    bounds = assessment['declared_assessment']['ranges']['one_time_implementation_cost']
    if bounds is None:
        return 'scenario_cost_unknown'
    if saved is None:
        return 'saved_budget_missing'
    if not isinstance(saved, dict) or saved.get('basis') not in ('estimate', 'approved_budget'):
        return 'saved_budget_invalid'
    if saved.get('currency') != currency:
        return 'currency_mismatch'
    amount = saved.get('amount')
    if type(amount) not in (int, float):
        return 'saved_budget_invalid'
    try:
        value = Fraction(str(amount))
    except (ValueError, ZeroDivisionError, OverflowError):
        return 'saved_budget_invalid'
    if value < 0:
        return 'saved_budget_invalid'
    lo, hi = map(Fraction, bounds)
    return 'below_scenario_range' if value < lo else 'above_scenario_range' if value > hi else 'within_scenario_range'


def reconcile(sources, packet_pin, proposals, review_pin, plan, planning_pin, ideas):
    report = planning.compare(sources, packet_pin, proposals, review_pin, plan)
    require(report['planning_review_sha256'] == planning_pin, 'Planning review changed')
    structural = batch.review_batch(sources, packet_pin, proposals)
    saved = inventory(ideas)
    relevant = [x for x in saved if x['lineage'].get('packet_sha256') == packet_pin]
    by_proposal = {}
    for x in relevant:
        pin = x['lineage'].get('proposal_sha256')
        if isinstance(pin, str):
            by_proposal.setdefault(pin, []).append(x)
    mapped, dispositions = {}, {}
    for item in structural['items']:
        i = item['item_index']
        if item['status'] != 'structurally_reviewed':
            dispositions[i] = 'not_a_distinct_reviewed_candidate'
            continue
        matches = by_proposal.get(item['proposal_sha256'], [])
        if not matches:
            dispositions[i] = 'not_recorded'
        elif len(matches) != 1:
            dispositions[i] = 'ambiguous_saved_lineage'
        else:
            x = matches[0]; lin = x['lineage']
            valid = (lin.get('format') == 'openworkgraph.ai-opportunity-handoff.v1'
                     and lin.get('structural_review_sha256') == item['review_sha256']
                     and lin.get('structural_review_only') is True
                     and all(lin.get(k) is False for k in ('semantic_correctness_verified', 'source_authenticity_verified', 'execution_authorized'))
                     and isinstance(lin.get('preview_sha256'), str) and HEX.fullmatch(lin['preview_sha256'])
                     and x['source'] == {'kind':'manual', 'reference':f"ai-proposal:{packet_pin}:{item['proposal_sha256']}"})
            dispositions[i] = 'matched' if valid else 'lineage_conflict'
            if valid:
                mapped[i] = x
    rows = []
    for item in report['candidates']:
        i = item['item_index']; x = mapped.get(i); a = item['assessment']
        dep_indexes = item['declared_dependency_item_indexes']
        expected = sorted(mapped[j]['idea_id'] for j in dep_indexes if j in mapped)
        unresolved = [j for j in dep_indexes if j not in mapped]
        if x is None or a is None:
            dependency = None
        else:
            actual = set(x['depends_on'])
            dependency = {'resolved_expected_idea_ids': expected,
                          'unresolved_candidate_indexes': unresolved,
                          'missing_saved_links': sorted(set(expected)-actual),
                          'saved_links_outside_scenario': sorted(actual-set(expected)),
                          'exactly_matches_declared_dependencies': not unresolved and set(expected) == actual}
        rows.append({'item_index':i, 'proposal_sha256':item['proposal_sha256'],
                     'structural_status':item['structural_status'], 'scenario_assessed':a is not None,
                     'mapping_status':dispositions[i],
                     'saved_idea':None if x is None else {k:x[k] for k in ('idea_id','revision','state','budget','depends_on','has_assessment')},
                     'budget_comparison':None if x is None else _budget(x['budget'],a,report['currency']),
                     'dependency_comparison':dependency})
    body = {'format':FORMAT, 'packet_sha256':packet_pin, 'batch_review_sha256':review_pin,
            'planning_review_sha256':planning_pin, 'currency':report['currency'],
            'portfolio_lineage_snapshot_sha256':contract._digest(relevant),
            'item_count':report['item_count'], 'matched_count':len(mapped), 'items':rows,
            'shared_scoped_citations':report['shared_scoped_citations'],
            'portfolio_written':False, 'execution_authorized':False, 'savings_verified':False,
            'interpretation':'Read-only snapshot. Matching lineage is not semantic verification. A budget is not actual spend. Scenario net hours and existing pipeline gross eligible hours are not interchangeable. External dependencies may be valid. Reload saved idea revisions before separate human decisions; no optimizer or approval invoked.'}
    return {**body, 'reconciliation_sha256':contract._digest(body)}
