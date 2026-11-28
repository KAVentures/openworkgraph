"""Session-independent validation for an explicitly reviewed loopback draft request.

The caller enforces dashboard authorization. No workflow execution or persistence.
"""
from . import ai_implementation_local_model as adapter
from . import ai_implementation_review as contract

FORMAT = 'openworkgraph.ai-implementation-local-handoff.v1'
_FIELDS = {'sources', 'expected_packet_sha256', 'max_bytes', 'model', 'port',
           'max_output_tokens', 'json_mode', 'confirm_local_inference'}


def generate(payload):
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError('exact local inference request required')
    if payload['confirm_local_inference'] is not True:
        raise ValueError('explicit local inference confirmation required')
    budget = payload['max_bytes']
    if type(budget) is not int or not 1024 <= budget <= adapter.MAX_PROMPT:
        raise ValueError('local model evidence budget exceeds bound')
    sources = payload['sources']
    packet = contract.prepare(sources, max_bytes=budget)
    if packet['packet_sha256'] != payload['expected_packet_sha256']:
        raise ValueError('selected evidence changed; prepare a new packet')
    proposal, receipt = adapter.propose(sources, model=payload['model'],
        port=payload['port'], max_context_bytes=budget,
        max_output_tokens=payload['max_output_tokens'], json_mode=payload['json_mode'],
        confirm_local_inference=True, timeout=120)
    return {'format': FORMAT, 'packet_sha256': packet['packet_sha256'],
            'proposal_json': contract._json(proposal).decode('utf-8'),
            'receipt': receipt, 'human_review_required': True,
            'semantic_correctness_verified': False, 'execution_authorized': False,
            'opportunity_created': False}
