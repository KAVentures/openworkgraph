import json
from copy import deepcopy

from shared import ai_context_compaction as compaction
from shared import ai_implementation_packet as packets
from shared import ai_implementation_review as contract
from test_ai_implementation_review import source, proposal
from test_ai_implementation_compact_inference import repetitive_sources


def test_exact_compact_external_download_and_original_review():
    sources = repetitive_sources()
    r = packets.preview(sources, 131072)
    d = r['compact_download']
    original = r['packet_json'].encode('utf-8')
    compact = d['packet_json'].encode('utf-8')
    decoded = json.loads(compact)
    assert compaction.canonical(compaction.expand(decoded, r['packet_sha256'])) == original
    assert decoded['compact_sha256'] == r['compaction']['compact_sha256']
    assert d['original_total_bytes'] == len(original) + len(r['instructions'].encode('utf-8'))
    assert d['compact_total_bytes'] == len(compact) + len(d['instructions'].encode('utf-8'))
    assert d['total_bytes_saved'] == d['original_total_bytes'] - d['compact_total_bytes'] > 0
    assert 'copy original_packet_sha256' in d['instructions']
    assert 'never a payload hash' in d['instructions']
    assert 'copy the exact value' in r['instructions']
    assert all(d[k] is False for k in ('token_savings_verified', 'model_quality_equivalence_verified', 'external_sharing_authorized'))
    assert contract.review(sources, proposal(sources))['packet_sha256'] == r['packet_sha256']


def test_no_offer_for_small_or_larger_budget():
    assert packets.preview([source()], 131072)['compact_download'] is None
    assert packets.preview(repetitive_sources(), 2097152)['compact_download'] is None


def test_instruction_overhead_can_eliminate_packet_saving():
    a = source()
    run = a['canonical_evidence'][0]
    template = deepcopy(run['events'][0])
    for size in range(500, 3000, 10):
        template['metadata'] = {'details': 'x' * size}
        run['events'] = [{**deepcopy(template), 'event_ref': f'event:{i:016x}'} for i in range(2)]
        run.update(observed_event_count=2, returned_event_count=2)
        r = packets.preview([a], 131072)
        if r['compaction']['available'] and r['compact_download'] is None:
            break
    else:
        raise AssertionError('Expected positive packet saving offset by guide overhead')
