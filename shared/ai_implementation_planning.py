"""Pre-implementation scenario review of a pinned external-AI proposal batch.

All economics and feasibility inputs are human-reviewed assumptions. No ranking,
portfolio writes, permission, actual savings claim, or model invocation.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import os
import re

from . import ai_implementation_batch as batch
from . import ai_implementation_review as contract

WORKLIST = 'openworkgraph.ai-implementation-planning-worklist.v1'
REPORT = 'openworkgraph.ai-implementation-planning-review.v1'
MAX_PLAN_BYTES = 128 * 1024
# Decimal strings keep exact arithmetic and avoid browser floating-point ambiguity.
NUMBER = re.compile(r'-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?\Z')
RANGES = {'cases_per_month': (0, 10_000_000), 'eligible_share': (0, 1),
          'net_human_minutes_saved_per_eligible_case': (-10_000, 10_000),
          'human_capacity_value_per_hour': (0, 1_000_000),
          'incremental_operating_cost_per_month': (0, 10**11),
          'one_time_implementation_cost': (0, 10**11)}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def _batch(sources, packet_pin, proposals, review_pin):
    r = batch.review_batch(sources, packet_pin, proposals)
    require(r['batch_review_sha256'] == review_pin, 'Original batch review pin changed')
    return r


def worklist(sources, packet_pin, proposals, review_pin, currency):
    r = _batch(sources, packet_pin, proposals, review_pin)
    require(isinstance(currency, str) and re.fullmatch(r'[A-Z]{3}', currency), 'Specify one currency code')
    return {'format': WORKLIST, 'packet_sha256': packet_pin, 'batch_review_sha256': review_pin,
            'currency': currency, 'confirm_human_reviewed_assumptions': False,
            'candidates': [{'item_index': row['item_index'], 'proposal_sha256': row['proposal_sha256'],
                            'assessment': None} for row in r['items'] if row['status'] == 'structurally_reviewed']}


def _interval(value, key):
    if value is None:
        return None
    require(isinstance(value, list) and len(value) == 2 and
            all(isinstance(v, str) and NUMBER.fullmatch(v) for v in value), 'Expected two exact decimal strings or null')
    lo, hi = map(Fraction, value)
    minimum, maximum = RANGES[key]
    require(minimum <= lo <= hi <= maximum, 'Reversed or out-of-range assumption')
    return lo, hi


def _mul(*values):
    if any(v is None for v in values):
        return None
    lo = hi = Fraction(1)
    for a, b in values:
        corners = (lo*a, lo*b, hi*a, hi*b)
        lo, hi = min(corners), max(corners)
    return lo, hi


def _sub(a, b):
    return None if a is None or b is None else (a[0]-b[1], a[1]-b[0])


def _show(value):
    return None if value is None else {'low': str(value[0]), 'high': str(value[1])}


def _assessment(value, index, allowed):
    if value is None:
        return None, []
    require(isinstance(value, dict) and set(value) == {'ranges', 'feasibility', 'risk',
            'depends_on_item_indexes', 'basis_note'}, 'Invalid planning assessment')
    require(value['feasibility'] in ('plausible', 'needs_review', 'blocked', 'unknown')
            and value['risk'] in ('low', 'medium', 'high', 'unknown'), 'Invalid declared feasibility or risk')
    note = value['basis_note']
    require(isinstance(note, str) and 0 < len(note.strip()) <= 2000 and len(note) <= 2000,
            'Record the human-reviewed basis and uncertainty')
    deps = value['depends_on_item_indexes']
    require(isinstance(deps, list) and len(deps) <= 19 and all(type(i) is int and i in allowed and i != index for i in deps)
            and len(deps) == len(set(deps)), 'Invalid declared dependencies')
    inputs = value['ranges']
    require(isinstance(inputs, dict) and set(inputs) == set(RANGES), 'Declare every range, using null for unknowns')
    values = {k: _interval(inputs[k], k) for k in RANGES}
    hours = _mul(values['cases_per_month'], values['eligible_share'],
                 values['net_human_minutes_saved_per_eligible_case'], (Fraction(1,60), Fraction(1,60)))
    capacity = _mul(hours, values['human_capacity_value_per_hour'])
    recurring = _sub(capacity, values['incremental_operating_cost_per_month'])
    first_year = _sub(_mul(recurring, (Fraction(12), Fraction(12))), values['one_time_implementation_cost'])
    return {'declared_assessment': value, 'unknown_ranges': [k for k in RANGES if values[k] is None],
            'modeled_net_human_hours_per_month': _show(hours), 'modeled_capacity_value_per_month': _show(capacity),
            'modeled_capacity_value_less_operating_cost_per_month': _show(recurring),
            'modeled_first_year_capacity_value_less_incremental_costs': _show(first_year)}, deps


def compare(sources, packet_pin, proposals, review_pin, plan):
    r = _batch(sources, packet_pin, proposals, review_pin)
    require(isinstance(plan, dict) and len(contract._json(plan)) <= MAX_PLAN_BYTES and set(plan) == {
        'format', 'packet_sha256', 'batch_review_sha256', 'currency', 'confirm_human_reviewed_assumptions', 'candidates'},
        'Invalid planning worklist')
    require(plan['format'] == WORKLIST and plan['packet_sha256'] == packet_pin and plan['batch_review_sha256'] == review_pin
            and plan['confirm_human_reviewed_assumptions'] is True, 'Exact pins and explicit assumption review required')
    require(isinstance(plan['currency'], str) and re.fullmatch(r'[A-Z]{3}', plan['currency']), 'Specify one currency code')
    allowed = {row['item_index']: row for row in r['items'] if row['status'] == 'structurally_reviewed'}
    rows = plan['candidates']
    require(isinstance(rows, list) and len(rows) == len(allowed), 'Retain every distinct reviewed candidate')
    assessments, graph = {}, {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'item_index', 'proposal_sha256', 'assessment'}, 'Invalid candidate row')
        i = row['item_index']
        require(type(i) is int and i in allowed and i not in assessments and
                row['proposal_sha256'] == allowed[i]['proposal_sha256'], 'Unknown, duplicate or stale candidate')
        assessments[i], graph[i] = _assessment(row['assessment'], i, allowed)
    # Reject cycles in declared candidate dependencies, rather than inventing a schedule.
    active, done = set(), set()
    def visit(i):
        require(i not in active, 'Declared dependency cycle')
        if i not in done:
            active.add(i)
            for j in graph[i]:
                visit(j)
            active.remove(i)
            done.add(i)
    for i in allowed:
        visit(i)
    citations = {i: batch._citations(proposals[i]) for i in allowed}
    overlap = [{'left_item_index': i, 'right_item_index': j, 'shared_scoped_citation_count': len(citations[i] & citations[j])}
               for i in allowed for j in allowed if i < j and citations[i] & citations[j]]
    candidates = []
    for row in r['items']:
        i = row['item_index']
        a = assessments.get(i)
        candidates.append({'item_index': i, 'proposal_sha256': row['proposal_sha256'], 'structural_status': row['status'],
                           'duplicate_of_index': row['duplicate_of_index'], 'assessment': a,
                           'declared_dependency_item_indexes': graph.get(i, []),
                           'dependencies_without_plausible_assessment': [j for j in graph.get(i, []) if assessments[j] is None
                               or assessments[j]['declared_assessment']['feasibility'] != 'plausible']})
    body = {'format': REPORT, 'packet_sha256': packet_pin, 'batch_review_sha256': review_pin,
            'planning_input_sha256': contract._digest(plan), 'currency': plan['currency'], 'item_count': r['item_count'],
            'distinct_reviewed_count': len(allowed), 'assessed_count': sum(a is not None for a in assessments.values()),
            'candidates': candidates, 'shared_scoped_citations': overlap, 'coverage_gaps': r['coverage_gaps'],
            'uncited_run_refs': r['uncited_run_refs'], 'portfolio_total': None,
            'portfolio_total_reason': 'Workload overlap, shared implementation costs and resource constraints are not resolved; even nonoverlapping citations do not establish independence.',
            'arithmetic': 'Exact rational interval bounds; low/high are fraction strings, not probabilities or forecasts.',
            'interpretation': 'Inputs are reviewed assumptions, not measured workload or costs. Net human minutes include review, rework and escalation; released capacity is not cash savings. No adoption, quality or risk gate is inferred.',
            'observed_workload_verified': False, 'actual_cost_verified': False, 'realized_savings_verified': False,
            'representative_coverage_verified': False, 'feasibility_verified': False, 'opportunity_ranking_performed': False,
            'portfolio_written': False, 'execution_authorized': False}
    return {**body, 'planning_review_sha256': contract._digest(body)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for mode in ('worklist', 'compare'):
        c = sub.add_parser(mode)
        c.add_argument('--source', action='append', required=True)
        for arg in ('packet-sha256', 'proposals', 'batch-review-sha256', 'output'):
            c.add_argument('--'+arg, required=True)
        c.add_argument('--confirm-local', action='store_true', required=True)
        c.add_argument('--currency' if mode == 'worklist' else '--plan', required=True)
    a = p.parse_args(argv)
    try:
        require(len(a.source) <= contract.MAX_BUNDLES, 'Too many sources')
        args = ([contract._read(x) for x in a.source], a.packet_sha256, contract._read(a.proposals), a.batch_review_sha256)
        result = worklist(*args, a.currency) if a.command == 'worklist' else compare(*args, contract._read(a.plan))
        raw = contract._json(result)
        require(len(raw) <= contract.MAX_BYTES, 'Output exceeds byte budget')
        fd = os.open(a.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError, OverflowError):
        p.exit(2, 'Planning review refused; check inputs, exact pins, assumptions and new output location.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
