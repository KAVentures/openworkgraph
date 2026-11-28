"""Synthetic, offline MCP knowledge retrieval benchmark (bytes, not tokens)."""
from __future__ import annotations

import argparse
from bisect import bisect_right
import hashlib
import json
from time import perf_counter

from mcp_server import knowledge_discovery_page as page
from mcp_server import task_knowledge_companion as companion


class Catalog:
    """Fixed AI-visible fixture, not a database or redaction simulation."""
    def __init__(self, count, match_every):
        self.rows = []
        self.expected = []
        self.reads = 0
        self.read_bytes = 0
        for i in range(count):
            match = bool(match_every and i % match_every == 0)
            key = f'process_{i:06d}'
            row = {'workflow_id': key, 'revision': 1,
                   'title': 'Invoice approval' if match else 'Inventory review',
                   'procedure': 'Inspect the selected record, validate the inputs, and record the result. ' * 14,
                   'format': 'openworkgraph.workflow-knowledge.v1',
                   'data_layer': 'user_reviewed_knowledge', 'review_basis': 'client_declared_user_confirmation',
                   'observed_evidence': False, 'organization_policy': False, 'execution_authorization': False}
            row['content_sha256'] = hashlib.sha256(companion.encoded(row)).hexdigest()
            self.rows.append(row)
            if match:
                self.expected.append(key)
        self.ids = [r['workflow_id'] for r in self.rows]
        self.index = dict(zip(self.ids, self.rows))

    def get(self, path, params):
        if path != '/v1/workflow-knowledge':
            raise ValueError('Unexpected fixture endpoint')
        key = params['workflow_id']
        if key:
            rows = [self.index[key]] if key in self.index else []
            more = False
        else:
            start = bisect_right(self.ids, params['after'])
            rows = self.rows[start:start + params['limit']]
            more = start + len(rows) < len(self.rows)
        payload = {'format': 'openworkgraph.workflow-knowledge.v1', 'workflows': rows,
                   'returned': len(rows), 'has_more': more,
                   'knowledge_is_observed_evidence': False, 'knowledge_is_execution_authorization': False}
        if not key:
            payload['next_after'] = rows[-1]['workflow_id'] if more else None
        self.reads += 1
        self.read_bytes += len(companion.encoded(payload))
        return payload


def run(count=1000, match_every=17, scan_limit=100, result_limit=20, adaptive=False):
    if type(count) is not int or not 1 <= count <= 10000 or type(match_every) is not int or match_every < 0:
        raise ValueError('Use 1–10000 records and a nonnegative match interval')
    page.request_args('', result_limit, 'invoice approval', True, '', scan_limit)
    catalog = Catalog(count, match_every)
    # Hypothetical baseline: deliver every full page to the AI. This is not a
    # claim that the legacy MCP tool supported a complete paginated scan.
    start = perf_counter()
    after = ''
    while True:
        raw = catalog.get('/v1/workflow-knowledge', {'workflow_id': '', 'after': after, 'limit': scan_limit})
        if not raw['has_more']:
            break
        after = raw['next_after']
    baseline = {'calls': catalog.reads, 'json_bytes': catalog.read_bytes,
                'local_seconds': perf_counter() - start}
    catalog.reads = catalog.read_bytes = 0
    start = perf_counter()
    after, candidates, pages, visible_bytes, scanned = '', [], 0, 0, 0
    current_limit = scan_limit
    while True:
        args, terms = page.request_args('', result_limit, 'invoice approval', True, after, current_limit)
        result = page.discover(catalog.get('/v1/workflow-knowledge', args), terms, result_limit, after, current_limit)
        candidates.extend(result['candidates'])
        pages += 1
        visible_bytes += len(companion.encoded(result))
        scanned += result['records_scanned']
        if not result['has_more']:
            break
        if result['next_after'] <= after or pages > count:
            raise RuntimeError('Nonadvancing cursor')
        after = result['next_after']
        if adaptive:
            current_limit = result['next_scan_limit_hint']
    found = [r['workflow_id'] for r in candidates]
    if found != catalog.expected:
        raise RuntimeError('Fixture match coverage or order changed')
    discovery_reads = catalog.reads
    discovery_read_bytes = catalog.read_bytes
    refs = [{k: r[k] for k in ('workflow_id', 'revision', 'content_sha256')} for r in candidates[:3]]
    selected = companion.read(refs, catalog.get)
    if selected is not None:
        visible_bytes += len(companion.encoded(selected))
        if [r['reference'] for r in selected['records']] != refs:
            raise RuntimeError('Selected references changed')
    return {'format': 'openworkgraph.synthetic-knowledge-retrieval-benchmark.v1',
            'catalog_records': count, 'match_every': match_every, 'expected_matches': len(catalog.expected),
            'returned_matches': len(found), 'duplicate_matches': len(found) - len(set(found)),
            'scan_limit': scan_limit, 'result_limit': result_limit, 'adaptive_scan_hint_used': adaptive, 'baseline': baseline,
            'discovery': {'calls': discovery_reads, 'scanned_record_occurrences': scanned,
                          'local_read_json_bytes': discovery_read_bytes, 'selected_exact_reads': len(refs),
                          'total_local_read_json_bytes': catalog.read_bytes,
                          'shortlist_and_selected_json_bytes': visible_bytes,
                          'local_seconds': perf_counter() - start},
            'model_token_savings_verified': False, 'model_quality_verified': False,
            'live_transport_latency_measured': False,
            'interpretation': 'Fixed synthetic AI-visible records; canonical JSON bytes before MCP annotations, not tokens or network bytes. Local time includes fixture lookup, encoding and filtering, not database, redaction, transport or model time. First three exact matches are a fixed benchmark choice, not semantic ranking.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=int, default=1000)
    parser.add_argument('--match-every', type=int, default=17, help='0 means no matches; 1 means all match')
    parser.add_argument('--scan-limit', type=int, default=100)
    parser.add_argument('--result-limit', type=int, default=20)
    parser.add_argument('--adaptive', action='store_true', help='Follow the explicit next-page scan size hint')
    a = parser.parse_args()
    print(json.dumps(run(a.records, a.match_every, a.scan_limit, a.result_limit, a.adaptive), indent=2))


if __name__ == '__main__':
    main()
