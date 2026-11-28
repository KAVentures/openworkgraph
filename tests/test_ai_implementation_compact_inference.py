import json
from copy import deepcopy

import pytest

from shared import ai_context_compaction as compaction
from shared import ai_implementation_local_model as adapter
from shared import ai_implementation_local_handoff as handoff
from shared import ai_implementation_packet as packets
from shared import ai_implementation_review as contract
from test_ai_implementation_review import source, proposal
from test_ai_implementation_local_model import endpoint, response, MODEL


def repetitive_sources():
    a = source()
    run = a['canonical_evidence'][0]
    template = deepcopy(run['events'][0])
    template['metadata'] = {'details': 'repeated observation é' * 100}
    run['events'] = [{**deepcopy(template), 'event_ref': f'event:{i:016x}'} for i in range(10)]
    run.update(observed_event_count=10, returned_event_count=10)
    return [a]


def test_original_and_compact_same_exact_evidence_through_http(endpoint):
    sources = repetitive_sources()
    port, calls = endpoint(response(sources))
    raw, a = adapter.propose(sources, model=MODEL, port=port, confirm_local_inference=True)
    compact, b = adapter.propose(sources, model=MODEL, port=port, confirm_local_inference=True,
                                 context_representation='compact')
    assert len(calls) == 2
    raw_context = json.loads(calls[0][2]['messages'][1]['content'])
    encoded = json.loads(calls[1][2]['messages'][1]['content'])
    assert compaction.expand(encoded, a['packet_sha256']) == raw_context
    assert 'original_packet_sha256' in calls[1][2]['messages'][0]['content']
    assert a['context_representation'] == 'original' and a['compact_sha256'] is None
    assert b['context_representation'] == 'compact' and b['compact_sha256'] == encoded['compact_sha256']
    assert raw == compact and a['proposal_sha256'] == b['proposal_sha256']
    assert b['context_bytes_saved'] == a['transmitted_context_bytes'] - b['transmitted_context_bytes'] > 0
    assert b['token_savings_verified'] is False and b['model_quality_equivalence_verified'] is False
    assert b['usage'] == a['usage']  # fixture, not evidence that real models save tokens


def test_compact_preview_and_handoff_match(endpoint):
    sources = repetitive_sources()
    preview = packets.preview(sources, 131072)
    assert preview['compaction']['available'] is True
    port, calls = endpoint(response(sources))
    result = handoff.generate({'sources': sources, 'expected_packet_sha256': preview['packet_sha256'],
        'max_bytes': 131072, 'model': MODEL, 'port': port, 'max_output_tokens': 4096,
        'json_mode': False, 'confirm_local_inference': True, 'context_representation': 'compact'})
    assert len(calls) == 1
    assert result['receipt']['compact_sha256'] == preview['compaction']['compact_sha256']
    assert result['receipt']['transmitted_context_bytes'] == preview['compaction']['compact_bytes']
    assert contract.review(sources, json.loads(result['proposal_json']))['status'] == 'structural_review_only'


def test_compact_never_silently_falls_back(endpoint):
    sources = [source()]
    port, calls = endpoint(response(sources))
    assert packets.preview(sources, 131072)['compaction']['available'] is False
    for representation in ('compact', 'invalid', None):
        with pytest.raises(ValueError):
            adapter.propose(sources, model=MODEL, port=port, confirm_local_inference=True,
                            context_representation=representation)
    assert not calls
    assert packets.preview(sources, 2097152)['compaction'] is None


def test_compact_still_rejects_bad_original_citation(endpoint):
    sources = repetitive_sources()
    reply = response(sources)
    p = proposal(sources)
    p['author_model'] = MODEL
    p['outcome']['evidence_refs'] = ['fake']
    reply['choices'][0]['message']['content'] = json.dumps(p)
    port, calls = endpoint(reply)
    with pytest.raises(ValueError):
        adapter.propose(sources, model=MODEL, port=port, confirm_local_inference=True,
                        context_representation='compact')
    assert len(calls) == 1
