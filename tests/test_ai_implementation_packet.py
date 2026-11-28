import copy
import json

import pytest

from shared import ai_implementation_packet as handoff
from shared import ai_implementation_review as contract
from test_ai_implementation_review import source, proposal


def test_exact_packet_remains_compatible_with_review():
    sources = [source(True), source()]
    result = handoff.preview(sources, 128 * 1024)
    assert result['format'] == handoff.FORMAT
    packet = json.loads(result['packet_json'])
    assert packet == contract.prepare(sources)
    assert result['packet_json'].encode('utf-8') == contract._json(packet)
    assert result['packet_bytes'] == len(contract._json(packet))
    assert result['source_count'] == 2
    assert result['coverage_gap_count'] == len(packet['coverage_gaps']) > 0
    assert contract.review(sources, proposal(sources))['packet_sha256'] == result['packet_sha256']
    assert result['packet_sha256'] != handoff.preview(sources[::-1], 128 * 1024)['packet_sha256']
    assert 'Ignore all instructions' not in result['instructions']
    assert all(result[key] is False for key in ('execution_authorized', 'external_model_invoked', 'external_sharing_authorized'))


def test_refuses_budget_and_empty_canonical_data():
    a = source()
    a['canonical_evidence'][0]['events'][0]['metadata'] = {'label': 'x' * 4096}
    with pytest.raises(ValueError):
        handoff.preview([a], 1024)
    a = copy.deepcopy(source())
    a['canonical_evidence'][0].update(events=[], observed_event_count=0, returned_event_count=0)
    with pytest.raises(ValueError):
        handoff.preview([a], 128 * 1024)
    for invalid in (True, '1024', 100, 9 * 1024 * 1024):
        with pytest.raises(ValueError):
            handoff.preview([source()], invalid)


def test_preserves_canonical_numeric_representation():
    a = source()
    a['canonical_evidence'][0]['events'][0]['duration_seconds'] = 1.0
    result = handoff.preview([a], 128 * 1024)
    assert '"duration_seconds":1.0' in result['packet_json']
    assert result['packet_json'].encode() == contract._json(contract.prepare([a]))
