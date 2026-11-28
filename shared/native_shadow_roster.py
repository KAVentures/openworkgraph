"""Freeze native shadow-test intent, then reconcile every planned case.

Uses the existing native trace reviewer; does not launch agents or grade outcomes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import native_shadow_trace_review as trace_review

PLAN = 'openworkgraph.native-shadow-roster.v1'
REPORT = 'openworkgraph.native-shadow-roster-review.v1'
PACKAGE = 'openworkgraph.ai-shadow-skill-package.v1'
MAX_CASES = 50
MAX_BYTES = 16 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(value):
    return trace_review.digest(value)


def pin(value):
    return isinstance(value, str) and trace_review.HEX.fullmatch(value) is not None


def case_rows(cases):
    require(isinstance(cases, list) and 1 <= len(cases) <= MAX_CASES,
            'Declare one to fifty test cases')
    seen, rows = set(), []
    for case in cases:
        require(isinstance(case, dict) and set(case) == {
            'case_id', 'expected_framework', 'reviewed_tool_labels'}, 'Invalid planned case')
        cid, framework, tools = (case[k] for k in (
            'case_id', 'expected_framework', 'reviewed_tool_labels'))
        require(isinstance(cid, str) and trace_review.CASE.fullmatch(cid) and cid not in seen,
                'Use distinct opaque case IDs')
        require(trace_review.label(framework) and isinstance(tools, list) and len(tools) <= 100
                and all(trace_review.label(t) for t in tools) and len(set(tools)) == len(tools),
                'Declare expected framework and unique reviewed tool labels')
        seen.add(cid)
        rows.append({'case_id': cid, 'expected_framework': framework,
                     'reviewed_tool_labels': sorted(tools)})
    return sorted(rows, key=lambda row: row['case_id'])


def freeze(manifest, package_pin, cases, *, confirmed):
    require(confirmed is True, 'Explicit roster confirmation required')
    require(isinstance(manifest, dict) and manifest.get('format') == PACKAGE
            and pin(package_pin) and manifest.get('package_sha256') == package_pin
            and digest({k: v for k, v in manifest.items() if k != 'package_sha256'}) == package_pin,
            'Exact staged skill manifest pin required')
    require(manifest.get('mode') == 'shadow_only' and all(manifest.get(k) is False for k in (
        'execution_authorized', 'deployment_verified', 'client_permissions_verified',
        'semantic_correctness_verified')), 'Expected unapproved shadow-only package')
    require(all(pin(manifest.get(k)) for k in ('packet_sha256', 'proposal_sha256', 'draft_sha256')),
            'Missing skill provenance pins')
    body = {'format': PLAN, 'package_sha256': package_pin,
            'packet_sha256': manifest['packet_sha256'], 'proposal_sha256': manifest['proposal_sha256'],
            'draft_sha256': manifest['draft_sha256'], 'cases': case_rows(cases),
            'execution_authorized': False}
    return {**body, 'plan_sha256': digest(body)}


def validate_plan(plan, expected_pin):
    require(isinstance(plan, dict) and set(plan) == {'format', 'package_sha256', 'packet_sha256',
        'proposal_sha256', 'draft_sha256', 'cases', 'execution_authorized', 'plan_sha256'},
        'Invalid frozen roster')
    require(plan['format'] == PLAN and plan['execution_authorized'] is False
            and pin(expected_pin) and plan['plan_sha256'] == expected_pin
            and digest({k: v for k, v in plan.items() if k != 'plan_sha256'}) == expected_pin
            and all(pin(plan[k]) for k in ('package_sha256', 'packet_sha256', 'proposal_sha256', 'draft_sha256')),
            'Frozen roster pin mismatch')
    require(plan['cases'] == case_rows(plan['cases']), 'Invalid canonical planned case population')
    return {c['case_id']: c for c in plan['cases']}


def worklist(plan, expected_pin):
    planned = validate_plan(plan, expected_pin)
    return [{'case_id': cid, 'root_execution_id': None} for cid in planned]


def review(plan, expected_pin, trace, trace_pin, assignments):
    planned = validate_plan(plan, expected_pin)
    require(isinstance(assignments, list) and len(assignments) == len(planned),
            'Assignments must cover the exact frozen roster')
    indexed, roots = {}, set()
    for row in assignments:
        require(isinstance(row, dict) and set(row) == {'case_id', 'root_execution_id'},
                'Invalid assignment')
        cid, root = row['case_id'], row['root_execution_id']
        require(isinstance(cid, str) and cid in planned and cid not in indexed,
                'Unknown or duplicate planned case')
        require(root is None or isinstance(root, str) and trace_review.RUN.fullmatch(root)
                and root not in roots, 'Invalid or reused root execution')
        indexed[cid] = root
        if root is not None:
            roots.add(root)
    require(isinstance(trace, dict) and pin(trace_pin) and digest(trace) == trace_pin,
            'Exact trace pin required')
    # The existing reviewer needs a nonempty returned run inventory. Explicit
    # empty snapshots are valid evidence of a coverage gap, not absence of work.
    if trace.get('executions') == []:
        require(trace.get('source') == 'canonical_agent_evidence'
                and trace.get('read_only') is True and trace.get('writes_performed') is False
                and trace.get('evidence_is_canonical') is True and trace.get('authoritative') is False
                and type(trace.get('returned')) is int and trace['returned'] == 0
                and type(trace.get('agent_execution_count_considered')) is int
                and trace['agent_execution_count_considered'] >= 0, 'Invalid empty trace inventory')
        inventory = {}
    else:
        inventory = trace_review.inventory(trace)
    declared = [{**planned[cid], 'root_execution_id': indexed[cid]}
                for cid in planned if indexed[cid] is not None]
    underlying = trace_review.review(trace, trace_pin, {
        'package_sha256': plan['package_sha256'], 'cases': declared}) if declared and inventory else None
    observations = {c['case_id']: c for c in underlying['cases']} if underlying else {}
    cases = []
    for cid in planned:
        root = indexed[cid]
        if root is None:
            status = 'not_assigned'
        elif cid not in observations or not observations[cid]['returned_execution_ids']:
            status = 'root_not_returned'
        else:
            status = observations[cid]['status']
        cases.append({'case_id': cid, 'root_execution_id': root, 'status': status,
                      'trace_observation': observations.get(cid)})
    statuses = ('not_assigned', 'root_not_returned', 'incomplete_observation',
                'observed_deviation_or_failure', 'no_deviation_in_returned_events')
    body = {'format': REPORT, 'plan_sha256': expected_pin, 'package_sha256': plan['package_sha256'],
            'trace_sha256': trace_pin, 'assignments_sha256': digest([
                {'case_id': cid, 'root_execution_id': indexed[cid]} for cid in planned]),
            'underlying_trace_review_sha256': underlying['review_sha256'] if underlying else None,
            'case_count': len(planned), 'counts': {s: sum(c['status'] == s for c in cases) for s in statuses},
            'cases': cases, 'unassigned_returned_execution_ids': underlying['unassigned_returned_execution_ids']
                if underlying else sorted(inventory),
            'response_run_limit_reached': trace['agent_execution_count_considered'] > len(inventory),
            'prospective_enrollment_verified': False, 'package_files_verified': False,
            'case_and_package_association_verified': False, 'skill_invocation_verified': False,
            'business_outcomes_verified': False, 'client_permissions_verified': False,
            'execution_authorized': False}
    return {**body, 'review_sha256': digest(body)}


def write(path, value):
    data = trace_review.canonical(value)
    require(len(data) <= MAX_BYTES, 'Output exceeds byte budget')
    fd = os.open(Path(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for mode, args in [('freeze', ('manifest', 'package-sha256', 'cases')),
                       ('worklist', ('plan', 'plan-sha256')),
                       ('review', ('plan', 'plan-sha256', 'trace', 'trace-sha256', 'assignments'))]:
        p = sub.add_parser(mode)
        for arg in (*args, 'output'):
            p.add_argument('--' + arg, required=True)
        p.add_argument('--confirm-local', action='store_true', required=True)
    a = parser.parse_args(argv)
    try:
        load = trace_review.load
        if a.command == 'freeze':
            result = freeze(load(a.manifest), a.package_sha256, load(a.cases), confirmed=a.confirm_local)
        elif a.command == 'worklist':
            result = worklist(load(a.plan), a.plan_sha256)
        else:
            result = review(load(a.plan), a.plan_sha256, load(a.trace), a.trace_sha256, load(a.assignments))
        write(a.output, result)
    except (ValueError, TypeError, KeyError, OSError, RecursionError, OverflowError):
        parser.exit(2, 'Shadow roster refused; check exact pins, inputs and new output location.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
