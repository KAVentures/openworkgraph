"""Explicit two-attempt local context experiment; never chooses or deploys a model."""
from __future__ import annotations

import argparse
import http.client
import os
from pathlib import Path
import time

from . import ai_context_compaction as compaction
from . import ai_implementation_local_model as adapter
from . import ai_implementation_review as contract

FORMAT = 'openworkgraph.ai-implementation-context-trial.v1'
ORDERS = ('original-first', 'compact-first')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def compare(sources, *, model, port=11434, max_context_bytes=128*1024,
            max_output_tokens=4096, timeout=120, json_mode=False,
            order='original-first', confirm_two_requests=False):
    """Run both predeclared arms once; failed calls stay in the denominator."""
    require(confirm_two_requests is True, 'Explicit consent for two model requests required')
    require(isinstance(model, str) and adapter.MODEL.fullmatch(model), 'Invalid model ID')
    require(type(json_mode) is bool and order in ORDERS, 'Invalid experiment settings')
    adapter._bounded_integer(port, 1, 65535, 'port')
    adapter._bounded_integer(max_context_bytes, 1024, adapter.MAX_PROMPT, 'context byte budget')
    adapter._bounded_integer(max_output_tokens, 128, 16384, 'output token budget')
    adapter._bounded_integer(timeout, 1, 300, 'timeout')
    packet = contract.prepare(sources, max_bytes=max_context_bytes)
    require(packet['event_count'] > 0, 'No canonical events to cite')
    compacted, metrics = compaction.compact(packet, packet['packet_sha256'])
    require(metrics['canonical_bytes_saved'] > 0, 'Compact representation is not smaller')
    require(compaction.canonical(compaction.expand(compacted, packet['packet_sha256'])) == contract._json(packet),
            'Exact expansion failed')
    arms = ('original', 'compact') if order == 'original-first' else ('compact', 'original')
    attempts = []
    for arm in arms:
        started = time.monotonic()
        try:
            proposal, receipt = adapter.propose(sources, model=model, port=port,
                max_context_bytes=max_context_bytes, max_output_tokens=max_output_tokens,
                timeout=timeout, confirm_local_inference=True, json_mode=json_mode,
                context_representation=arm)
        except (ValueError, OSError, UnicodeError, RecursionError, http.client.HTTPException) as exc:
            # Never include response body, raw evidence, exception message or endpoint URL.
            attempts.append({'representation': arm, 'status': 'proposal_not_validated',
                'error_class': type(exc).__name__, 'elapsed_ms': round((time.monotonic()-started)*1000),
                'proposal': None, 'receipt': None})
            continue
        attempts.append({'representation': arm, 'status': 'structurally_validated_proposal',
            'error_class': None, 'elapsed_ms': round((time.monotonic()-started)*1000),
            'proposal': proposal, 'receipt': receipt})
    by_arm = {a['representation']: a for a in attempts}
    a, b = by_arm['original']['receipt'], by_arm['compact']['receipt']
    def delta(key, usage=False):
        if a is None or b is None:
            return None
        left, right = (a['usage'].get(key), b['usage'].get(key)) if usage else (a.get(key), b.get(key))
        return left-right if type(left) is int and type(right) is int else None
    body = {'format': FORMAT, 'packet_sha256': packet['packet_sha256'],
        'compact_sha256': metrics['compact_sha256'], 'requested_model': model,
        'settings': {'port': port, 'max_context_bytes': max_context_bytes,
                     'max_output_tokens': max_output_tokens, 'timeout_seconds': timeout,
                     'json_mode': json_mode, 'order': order},
        'planned_attempt_count': 2, 'attempts': attempts,
        'structurally_validated_count': sum(a['receipt'] is not None for a in attempts),
        'planned_evidence_bytes_saved': metrics['canonical_bytes_saved'],
        'original_minus_compact': {'request_bytes': delta('request_bytes'),
            'transmitted_context_bytes': delta('transmitted_context_bytes'),
            'elapsed_ms': delta('elapsed_ms'), 'prompt_tokens': delta('prompt_tokens', True),
            'completion_tokens': delta('completion_tokens', True), 'total_tokens': delta('total_tokens', True)},
        'both_receipts_available': a is not None and b is not None,
        'independent_quality_review_required': True, 'model_quality_equivalence_verified': False,
        'token_savings_verified': False, 'actual_cost_verified': False,
        'model_identity_independently_verified': False, 'inference_location_verified': False,
        'business_outcomes_verified': False, 'execution_authorized': False,
        'interpretation': 'Single ordered pair; endpoint-reported measurements only. Cache, ordering, backend and stochastic effects uncontrolled. Missing telemetry is unknown; failed calls may incur cost. No rollout or representation selection.'}
    return {**body, 'trial_sha256': contract._digest(body)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', action='append', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--port', type=int, default=11434)
    p.add_argument('--max-context-bytes', type=int, default=128*1024)
    p.add_argument('--max-output-tokens', type=int, default=4096)
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--json-mode', action='store_true')
    p.add_argument('--order', choices=ORDERS, default='original-first')
    p.add_argument('--confirm-two-requests', action='store_true', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args(argv)
    try:
        require(len(args.source) <= contract.MAX_BUNDLES, 'Too many sources')
        sources = [contract._read(x) for x in args.source]
        dest = Path(args.out)
        require(not dest.exists() and not dest.is_symlink() and dest.parent.is_dir(), 'Use a new local file')
        # Reserve the output before inference to avoid spending on an unavailable destination.
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            result = compare(sources, model=args.model, port=args.port,
                max_context_bytes=args.max_context_bytes, max_output_tokens=args.max_output_tokens,
                timeout=args.timeout, json_mode=args.json_mode, order=args.order,
                confirm_two_requests=args.confirm_two_requests)
            stream.write(contract._json(result) + b'\n')
    except (ValueError, OSError, UnicodeError, RecursionError, http.client.HTTPException):
        p.exit(2, 'Context trial refused; inspect inputs and discard any incomplete output.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
