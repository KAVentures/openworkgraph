import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from shared import ai_implementation_local_handoff as handoff
from shared import ai_implementation_review as contract
from test_ai_implementation_review import source, proposal


@pytest.fixture
def listener():
    sources = [source(True)]
    answer = proposal(sources)
    answer['author_model'] = 'local-test:1'
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body = json.dumps({'model': 'local-test:1', 'choices': [{'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(answer)}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = {'sources': sources, 'expected_packet_sha256': contract.prepare(sources)['packet_sha256'],
               'max_bytes': 131072, 'model': 'local-test:1', 'port': server.server_port,
               'max_output_tokens': 4096, 'json_mode': False, 'confirm_local_inference': True}
    try:
        yield payload, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_exact_packet_to_model_to_review(listener):
    payload, calls = listener
    result = handoff.generate(payload)
    assert len(calls) == 1
    assert json.loads(calls[0]['messages'][1]['content']) == contract.prepare(payload['sources'])
    assert result['packet_sha256'] == payload['expected_packet_sha256']
    review = contract.review(payload['sources'], json.loads(result['proposal_json']))
    assert review['proposal_sha256'] == result['receipt']['proposal_sha256']
    assert result['receipt']['usage']['total_tokens'] == 150
    assert result['receipt']['actual_cost_verified'] is False
    assert result['human_review_required'] is True
    assert all(result[key] is False for key in ('execution_authorized', 'semantic_correctness_verified', 'opportunity_created'))


@pytest.mark.parametrize('change', [
    {'expected_packet_sha256': '0' * 64}, {'confirm_local_inference': False},
    {'max_bytes': 8388608}, {'max_bytes': True}, {'model': 'invalid model!'},
    {'max_output_tokens': 100000}, {'port': 0}, {'json_mode': 'yes'}, {'url': 'https://example.invalid'}])
def test_invalid_requests_never_contact_listener(listener, change):
    payload, calls = listener
    with pytest.raises(ValueError):
        handoff.generate({**payload, **change})
    assert not calls
