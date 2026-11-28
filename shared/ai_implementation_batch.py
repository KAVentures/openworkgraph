"""Review multiple independently authored proposals against one evidence packet.

No ranking, model invocation, storage, portfolio write or deployment occurs.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import ai_implementation_review as contract

FORMAT = 'openworkgraph.ai-implementation-batch-review.v1'
MAX_PROPOSALS = 20
MAX_BATCH_BYTES = 2 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def _citations(proposal):
    claims = [proposal['outcome'], *proposal['steps'], *proposal['exceptions'],
              *proposal['execution_options']]
    return {ref for claim in claims for ref in claim['evidence_refs']}


def review_batch(sources, expected_packet_sha256, proposals):
    """Retain the complete returned population, including rejected items."""
    require(isinstance(proposals, list) and 1 <= len(proposals) <= MAX_PROPOSALS,
            'Select one to twenty proposal items')
    require(len(contract._json(proposals)) <= MAX_BATCH_BYTES, 'Proposal batch exceeds byte budget')
    packet = contract.prepare(sources)
    require(packet['packet_sha256'] == expected_packet_sha256, 'Exact evidence packet pin required')
    runs = {run['ref']: run for bundle in packet['bundles'] for run in bundle['runs']}
    known = {event['ref'] for run in runs.values() for event in run['events']}
    first, items, cited = {}, [], set()
    for index, proposal in enumerate(proposals):
        pin = contract._digest(proposal)
        row = {'item_index': index, 'proposal_sha256': pin, 'duplicate_of_index': None,
               'status': 'structural_review_refused', 'review': None, 'review_sha256': None}
        if pin in first:
            row.update(status='exact_duplicate', duplicate_of_index=first[pin])
        else:
            first[pin] = index
            try:
                review = contract.review(sources, proposal)
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
                pass  # Never echo untrusted input or guess at its semantic meaning.
            else:
                require(review['proposal_sha256'] == pin and review['packet_sha256'] == expected_packet_sha256,
                        'Unexpected structural review pin')
                row.update(status='structurally_reviewed', review=review,
                           review_sha256=contract._digest(review))
                cited.update(_citations(proposal))
        items.append(row)
    statuses = ('structurally_reviewed', 'structural_review_refused', 'exact_duplicate')
    body = {'format': FORMAT, 'packet_sha256': expected_packet_sha256,
            'batch_sha256': contract._digest(proposals), 'item_count': len(items),
            'counts': {s: sum(r['status'] == s for r in items) for s in statuses},
            'items': items, 'selected_event_count': len(known), 'distinct_cited_event_count': len(cited),
            'uncited_run_refs': [ref for ref in runs if not any(c.startswith(ref + '/') for c in cited)],
            'coverage_gaps': packet['coverage_gaps'], 'human_review_required': True,
            'semantic_correctness_verified': False, 'source_authenticity_verified': False,
            'representative_coverage_verified': False, 'opportunity_ranking_performed': False,
            'portfolio_written': False, 'execution_authorized': False,
            'interpretation': 'Citation coverage describes only the selected evidence, not discovery completeness or business meaning. Exact duplicates are not semantic deduplication. No item is automatically selected.'}
    return {**body, 'batch_review_sha256': contract._digest(body)}


def select(sources, packet_pin, proposals, review_pin, item_index):
    """Return one explicitly selected original proposal; existing review still required."""
    report = review_batch(sources, packet_pin, proposals)
    require(report['batch_review_sha256'] == review_pin, 'Batch changed; review again')
    require(type(item_index) is int and 0 <= item_index < len(proposals), 'Invalid item index')
    row = report['items'][item_index]
    require(row['status'] == 'structurally_reviewed', 'Select a distinct structurally reviewed item')
    return proposals[item_index]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for mode in ('review', 'select'):
        cmd = sub.add_parser(mode)
        cmd.add_argument('--source', action='append', required=True)
        cmd.add_argument('--packet-sha256', required=True)
        cmd.add_argument('--proposals', required=True)
        cmd.add_argument('--output', required=True)
        cmd.add_argument('--confirm-local', action='store_true', required=True)
        if mode == 'select':
            cmd.add_argument('--batch-review-sha256', required=True)
            cmd.add_argument('--item-index', type=int, required=True)
    a = p.parse_args(argv)
    try:
        require(len(a.source) <= contract.MAX_BUNDLES, 'Too many sources')
        sources = [contract._read(path) for path in a.source]
        proposals = contract._read(a.proposals)
        value = (review_batch(sources, a.packet_sha256, proposals) if a.command == 'review'
                 else select(sources, a.packet_sha256, proposals, a.batch_review_sha256, a.item_index))
        raw = contract._json(value)
        require(len(raw) <= contract.MAX_BYTES, 'Output exceeds byte budget')
        fd = os.open(Path(a.output), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError, OverflowError):
        p.exit(2, 'Batch review refused; check inputs, independent pins and new output location.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
