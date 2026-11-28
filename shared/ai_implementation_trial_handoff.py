"""Pin a deliberately authorized dashboard trial to the prepared evidence."""
from . import ai_context_compaction as compaction
from . import ai_implementation_context_trial as trial
from . import ai_implementation_local_model as adapter
from . import ai_implementation_review as contract

FORMAT = 'openworkgraph.ai-implementation-context-trial-handoff.v1'
FIELDS = {'sources', 'expected_packet_sha256', 'expected_compact_sha256', 'max_bytes',
          'model', 'port', 'max_output_tokens', 'json_mode', 'order', 'confirm_two_requests'}


def run(payload):
    trial.require(isinstance(payload, dict) and set(payload) == FIELDS, 'Exact paired trial request required')
    trial.require(payload['confirm_two_requests'] is True, 'Explicit two-request confirmation required')
    budget = adapter._bounded_integer(payload['max_bytes'], 1024, adapter.MAX_PROMPT, 'context byte budget')
    packet = contract.prepare(payload['sources'], max_bytes=budget)
    trial.require(packet['packet_sha256'] == payload['expected_packet_sha256'], 'Prepared evidence changed')
    _, metrics = compaction.compact(packet, packet['packet_sha256'])
    trial.require(metrics['compact_sha256'] == payload['expected_compact_sha256']
                  and metrics['canonical_bytes_saved'] > 0, 'Prepared compact evidence changed or does not save bytes')
    result = trial.compare(payload['sources'], model=payload['model'], port=payload['port'],
        max_context_bytes=budget, max_output_tokens=payload['max_output_tokens'], timeout=120,
        json_mode=payload['json_mode'], order=payload['order'], confirm_two_requests=True)
    trial.require(result['packet_sha256'] == packet['packet_sha256'] and
                  result['compact_sha256'] == metrics['compact_sha256'], 'Trial evidence pin mismatch')
    # Keep original canonical JSON strings for export, not browser-reserialized objects.
    return {'format': FORMAT, 'trial_sha256': result['trial_sha256'],
            'trial_json': contract._json(result).decode('utf-8'),
            'proposal_json': {a['representation']: contract._json(a['proposal']).decode('utf-8')
                              if a['proposal'] is not None else None for a in result['attempts']},
            'execution_authorized': False, 'independent_quality_review_required': True}
