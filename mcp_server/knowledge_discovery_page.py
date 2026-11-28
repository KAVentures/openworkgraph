"""Bounded forward navigation over AI-visible reviewed knowledge pages."""
from . import knowledge_discovery as base


def request_args(workflow_id, limit, query, summary_only, after, scan_limit):
    params, terms = base.request_args(workflow_id, limit, query, summary_only)
    base.require(not workflow_id, 'Discovery pages cannot select an exact workflow ID')
    base.require(isinstance(after, str) and (not after or base.ID.fullmatch(after)), 'Invalid discovery cursor')
    base.require(type(scan_limit) is int and 1 <= scan_limit <= base.MAX_SCAN, 'Scan limit must be 1–100')
    return {**params, 'after': after, 'limit': scan_limit}, terms


def discover(response, terms, limit, after, scan_limit):
    result = base.discover(response, terms, limit)
    records = response['workflows']
    ids = [r['workflow_id'] for r in records]
    base.require(len(ids) <= scan_limit and ids == sorted(ids) and all(i > after for i in ids), 'Unexpected catalog page order or bounds')
    cursor = response.get('next_after')
    base.require('next_after' in response and ((bool(ids) and cursor == ids[-1]) if response['has_more'] else cursor is None), 'Invalid catalog continuation')
    # Resume after the last returned match when the shortlist was cut short;
    # otherwise advance past all scanned records, even on a zero-match page.
    if result['matches_truncated']:
        cursor = result['candidates'][-1]['workflow_id']
    return {**result, 'scan_after': after, 'scan_limit': scan_limit,
            'next_after': cursor, 'has_more': cursor is not None,
            'atomic_catalog_snapshot_verified': False,
            'interpretation': result['interpretation'] + ' Continue using next_after with the same query. Each page is a separate read; inserts, deletions and revisions may change the inventory.'}
