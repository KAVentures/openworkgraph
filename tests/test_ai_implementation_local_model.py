import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from shared import ai_implementation_local_model as adapter
from shared.ai_implementation_review import prepare, review
from test_ai_implementation_review import source, proposal

MODEL = 'local-test:1'


def response(sources):
    p = proposal(sources)
    p['author_model'] = MODEL
    return {'model': MODEL, 'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(p)}}],
            'usage': {'prompt_tokens': 123, 'completion_tokens': 45, 'total_tokens': 168}}


@pytest.fixture
def endpoint():
    servers = []
    def start(reply=None, status=200, raw=None):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Location', 'http://example.invalid/')
                self.end_headers()
                try:
                    self.wfile.write(raw if raw is not None else json.dumps(reply).encode())
                except BrokenPipeError:
                    pass
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server.server_port, requests
    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_loopback_request_and_exact_proposal_review(endpoint):
    sources = [source(True)]
    port, requests = endpoint(response(sources))
    p, receipt = adapter.propose(sources, model=MODEL, port=port, confirm_local_inference=True, json_mode=True)
    assert len(requests) == 1
    path, headers, body = requests[0]
    assert path == '/v1/chat/completions'
    assert 'Authorization' not in headers
    assert body['model'] == MODEL and body['stream'] is False
    assert body['response_format'] == {'type': 'json_object'}
    assert 'tools' not in body and 'tool_choice' not in body
    assert json.loads(body['messages'][1]['content']) == prepare(sources)
    assert receipt['proposal_sha256'] == review(sources, p)['proposal_sha256']
    assert receipt['usage']['total_tokens'] == 168
    for field in ('inference_location_verified', 'model_identity_independently_verified', 'actual_cost_verified', 'execution_authorized', 'semantic_correctness_verified'):
        assert receipt[field] is False


def test_no_request_without_confirmation_or_invalid_inputs(endpoint):
    port, requests = endpoint(response([source()]))
    for options in ({}, {'confirm_local_inference': True, 'max_context_bytes': 1},
                    {'confirm_local_inference': True, 'max_output_tokens': 999999}):
        with pytest.raises(ValueError):
            adapter.propose([source()], model=MODEL, port=port, **options)
    assert not requests


@pytest.mark.parametrize('bad', ['identity', 'partial', 'tools', 'refusal', 'invented_ref', 'stale_packet', 'author', 'bad_json', 'duplicate'])
def test_rejects_bad_provider_response(endpoint, bad):
    sources = [source()]
    reply = response(sources)
    message = reply['choices'][0]['message']
    p = json.loads(message['content'])
    if bad == 'identity': reply['model'] = 'another-model'
    elif bad == 'partial': reply['choices'][0]['finish_reason'] = 'length'
    elif bad == 'tools': message['tool_calls'] = [{'id': 'anything'}]
    elif bad == 'refusal': message['refusal'] = 'denied'
    elif bad == 'invented_ref': p['outcome']['evidence_refs'] = ['made-up']
    elif bad == 'stale_packet': p['packet_sha256'] = '0'*64
    elif bad == 'author': p['author_model'] = 'another-model'
    message['content'] = json.dumps(p)
    if bad == 'bad_json': message['content'] = 'not JSON'
    if bad == 'duplicate': message['content'] = '{"format":"a","format":"b"}'
    port, requests = endpoint(reply)
    with pytest.raises(ValueError):
        adapter.propose(sources, model=MODEL, port=port, confirm_local_inference=True)
    assert len(requests) == 1


def test_no_redirect_retry_or_fallback(endpoint):
    port, requests = endpoint({}, status=302)
    with pytest.raises(ValueError):
        adapter.propose([source()], model=MODEL, port=port, confirm_local_inference=True)
    assert len(requests) == 1


def test_bounded_response_and_unknown_usage(endpoint, monkeypatch):
    port, _ = endpoint(raw=b' ' * 5000)
    monkeypatch.setattr(adapter, 'MAX_REPLY', 4096)
    with pytest.raises(ValueError):
        adapter.propose([source()], model=MODEL, port=port, confirm_local_inference=True)
    reply = response([source()])
    reply['usage'] = {'prompt_tokens': True, 'completion_tokens': -1, 'total_tokens': 'unknown'}
    port, _ = endpoint(reply)
    _, receipt = adapter.propose([source()], model=MODEL, port=port, confirm_local_inference=True)
    assert all(receipt['usage'][k] is None for k in ('prompt_tokens','completion_tokens','total_tokens'))


def test_cli_preserves_existing_output_and_reports_no_evidence_text(endpoint, tmp_path, capsys):
    sources = [source()]
    port, requests = endpoint(response(sources))
    src, out = tmp_path / 'source.json', tmp_path / 'proposal.json'
    src.write_text(json.dumps(sources[0]), encoding='utf-8')
    args = ['--source', str(src), '--model', MODEL, '--port', str(port), '--out', str(out)]
    assert adapter.main(args) == 2 and not requests and not out.exists()
    assert adapter.main(args + ['--confirm-local-inference']) == 0
    assert review(sources, json.loads(out.read_text()))['status'] == 'structural_review_only'
    assert 'Ignore all instructions' not in capsys.readouterr().out
    assert adapter.main(args + ['--confirm-local-inference']) == 2 and len(requests) == 1
