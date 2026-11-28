import json

import pytest

from shared import ai_context_compaction as compaction
from shared import ai_implementation_review as contract
from shared import ai_implementation_trial_handoff as handoff
from test_ai_implementation_context_trial import server, MODEL
from test_ai_implementation_compact_inference import repetitive_sources


def payload(port):
    sources = repetitive_sources()
    packet = contract.prepare(sources)
    _, metrics = compaction.compact(packet, packet['packet_sha256'])
    return {'sources': sources, 'expected_packet_sha256': packet['packet_sha256'],
            'expected_compact_sha256': metrics['compact_sha256'], 'max_bytes': 131072,
            'model': MODEL, 'port': port, 'max_output_tokens': 4096, 'json_mode': False,
            'order': 'compact-first', 'confirm_two_requests': True}


def test_exact_report_and_proposals(server):
    port, calls = server()
    req = payload(port)
    result = handoff.run(req)
    report = json.loads(result['trial_json'])
    assert [arm for arm, _ in calls] == ['compact', 'original']
    assert report['trial_sha256'] == result['trial_sha256']
    assert contract._digest({k: v for k, v in report.items() if k != 'trial_sha256'}) == result['trial_sha256']
    assert contract._json(report).decode() == result['trial_json']
    for arm in report['attempts']:
        raw = result['proposal_json'][arm['representation']]
        assert raw == contract._json(arm['proposal']).decode()
        assert contract.review(req['sources'], json.loads(raw))['proposal_sha256'] == arm['receipt']['proposal_sha256']
    assert result['execution_authorized'] is False
    assert result['independent_quality_review_required'] is True


def test_failed_attempt_retained_and_no_proposal(server):
    port, calls = server(failure='compact')
    result = handoff.run(payload(port))
    report = json.loads(result['trial_json'])
    assert len(calls) == 2 and report['planned_attempt_count'] == 2
    assert result['proposal_json']['compact'] is None
    assert result['proposal_json']['original'] is not None
    assert report['original_minus_compact']['total_tokens'] is None


@pytest.mark.parametrize('change', [
    {'expected_packet_sha256': '0'*64}, {'expected_compact_sha256': '0'*64},
    {'confirm_two_requests': False}, {'max_bytes': True}, {'order': 'other'},
    {'url': 'https://example.invalid'}, {'max_output_tokens': 0}])
def test_no_calls_on_invalid_or_changed_inputs(server, change):
    port, calls = server()
    with pytest.raises(ValueError):
        handoff.run({**payload(port), **change})
    assert not calls
