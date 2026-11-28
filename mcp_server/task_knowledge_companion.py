"""Explicit reviewed knowledge companion for an existing task-context read."""
import hashlib
import json
from . import knowledge_discovery as kd

MAX_BYTES = 128 * 1024
MAX_REFS = 3


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def validate(refs):
    if refs is None:
        return []
    kd.require(isinstance(refs, list) and len(refs) <= MAX_REFS, 'Select at most three exact reviewed knowledge references')
    seen = set()
    for r in refs:
        kd.require(isinstance(r, dict) and set(r) == {'workflow_id', 'revision', 'content_sha256'}, 'Exact knowledge reference required')
        key = r['workflow_id']
        kd.require(isinstance(key, str) and kd.ID.fullmatch(key) and key not in seen and type(r['revision']) is int and r['revision'] >= 1
                   and isinstance(r['content_sha256'], str) and kd.HEX.fullmatch(r['content_sha256']), 'Invalid or duplicate knowledge reference')
        seen.add(key)
    return [dict(r) for r in refs]


def read(refs, get):
    refs = validate(refs)
    if not refs:
        return None
    rows = []
    for ref in refs:
        data = get('/v1/workflow-knowledge', {'workflow_id': ref['workflow_id'], 'limit': 1})
        # The existing discovery validator checks the AI-visible envelope and authority flags.
        index = kd.discover(data, [], 1)
        kd.require(data['has_more'] is False and index['returned'] == 1, 'Selected knowledge is missing or ambiguous; reload')
        record = data['workflows'][0]
        kd.require(all(record.get(k) == v for k, v in ref.items()), 'Selected knowledge revision or reference changed; reload')
        kd.require(record.get('format') == 'openworkgraph.workflow-knowledge.v1' and record.get('data_layer') == 'user_reviewed_knowledge'
                   and record.get('review_basis') == 'client_declared_user_confirmation', 'Expected explicitly reviewed knowledge')
        rows.append({'reference': ref, 'ai_visible_api_sha256': hashlib.sha256(encoded(data)).hexdigest(),
                     'reviewed_record': json.loads(encoded(record))})
        kd.require(len(encoded(rows)) <= MAX_BYTES, 'Knowledge companion exceeds 128 KiB; select fewer records')
    result = {'format': 'openworkgraph.task-knowledge-companion.v1', 'records': rows,
              'selection_basis': 'explicit_caller_references', 'relevance_verified': False,
              'content_digest_verified': False, 'atomic_snapshot_verified': False,
              'observed_evidence': False, 'organization_policy': False, 'execution_authorized': False,
              'interpretation': 'Separate AI-visible reviewed context from independent reads; compare with observed procedure and declared policy. Stored digest references are matched, not reverified after redaction. Resolve conflicts and missing approval with the user. No applicability or permission is established.'}
    kd.require(len(encoded(result)) <= MAX_BYTES, 'Knowledge companion exceeds 128 KiB; select fewer records')
    return result
