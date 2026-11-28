import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from shared import ai_implementation_context_trial as trial
from shared import ai_implementation_review as contract
from test_ai_implementation_review import source, proposal
from test_ai_implementation_compact_inference import repetitive_sources

MODEL = 'local-test:1'


@pytest.fixture
def server():
    active = []
    def start(*, failure=None, missing_usage=False):
        calls = []
        sources = repetitive_sources()
        answer = proposal(sources)
        answer['author_model'] = MODEL
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                context = json.loads(payload['messages'][1]['content'])
                arm = 'compact' if 'original_packet_sha256' in context else 'original'
                calls.append((arm, payload))
                if arm == failure:
                    self.send_response(503); self.end_headers(); return
                usage = {'prompt_tokens': 100 if arm == 'original' else 60,
                         'completion_tokens': 20, 'total_tokens': 120 if arm == 'original' else 80}
                if missing_usage and arm == 'compact': usage = {}
                body = json.dumps({'model': MODEL, 'choices': [{'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': json.dumps(answer)}}], 'usage': usage}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_): pass
        listener = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = Thread(target=listener.serve_forever, daemon=True); worker.start()
        active.append((listener, worker))
        return listener.server_port, calls
    yield start
    for listener, worker in active:
        listener.shutdown(); listener.server_close(); worker.join(timeout=3)


@pytest.mark.parametrize('order', trial.ORDERS)
def test_exact_pair_and_reported_usage(server, order):
    sources = repetitive_sources()
    port, calls = server()
    r = trial.compare(sources, model=MODEL, port=port, order=order, confirm_two_requests=True)
    assert [arm for arm, _ in calls] == [a['representation'] for a in r['attempts']]
    assert calls[0][0] == order.split('-')[0]
    assert r['planned_attempt_count'] == r['structurally_validated_count'] == 2
    for a in r['attempts']:
        assert contract.review(sources, a['proposal'])['proposal_sha256'] == a['receipt']['proposal_sha256']
    assert r['original_minus_compact']['prompt_tokens'] == 40
    assert r['original_minus_compact']['total_tokens'] == 40
    assert r['original_minus_compact']['transmitted_context_bytes'] == r['planned_evidence_bytes_saved'] > 0
    assert r['trial_sha256'] == contract._digest({k:v for k,v in r.items() if k != 'trial_sha256'})
    assert r['independent_quality_review_required'] is True
    assert all(r[k] is False for k in ('token_savings_verified', 'model_quality_equivalence_verified', 'actual_cost_verified', 'execution_authorized'))


@pytest.mark.parametrize('failed', ['original', 'compact'])
def test_failed_attempt_retained_without_retry(server, failed):
    port, calls = server(failure=failed)
    r = trial.compare(repetitive_sources(), model=MODEL, port=port, confirm_two_requests=True)
    assert len(calls) == 2 and r['structurally_validated_count'] == 1
    bad = next(a for a in r['attempts'] if a['representation'] == failed)
    assert bad['status'] == 'proposal_not_validated' and bad['receipt'] is None
    assert not r['both_receipts_available'] and all(v is None for v in r['original_minus_compact'].values())


def test_unknown_usage_not_zero(server):
    port, _ = server(missing_usage=True)
    r = trial.compare(repetitive_sources(), model=MODEL, port=port, confirm_two_requests=True)
    assert r['both_receipts_available']
    assert r['original_minus_compact']['prompt_tokens'] is None
    assert r['original_minus_compact']['request_bytes'] > 0


def test_preflight_refusals_make_no_calls(server):
    port, calls = server()
    for kwargs in ({}, {'confirm_two_requests':True,'order':'random'},
                   {'confirm_two_requests':True,'max_output_tokens':0}):
        with pytest.raises(ValueError): trial.compare(repetitive_sources(), model=MODEL, port=port, **kwargs)
    with pytest.raises(ValueError): trial.compare([source()], model=MODEL, port=port, confirm_two_requests=True)
    assert not calls


def test_cli_exclusive_file(server, tmp_path):
    port, calls = server()
    source_path, out = tmp_path/'source.json', tmp_path/'trial.json'
    source_path.write_text(json.dumps(repetitive_sources()[0]), encoding='utf-8')
    argv = ['--source', str(source_path), '--model', MODEL, '--port', str(port), '--out', str(out), '--confirm-two-requests']
    assert trial.main(argv) == 0
    assert json.loads(out.read_text())['structurally_validated_count'] == 2
    with pytest.raises(SystemExit) as exc: trial.main(argv)
    assert exc.value.code == 2 and len(calls) == 2
