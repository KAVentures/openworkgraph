"""Portable, offline review of external AI proposals against selected OWG evidence.

No capture, model invocation, credentials, source reads, execution or policy writes.
Only already-authorized, explicitly supplied local workflow-evidence responses are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SOURCE = 'openworkgraph.workflow-evidence.v1'
PACKET = 'openworkgraph.ai-implementation-evidence.v1'
PROPOSAL = 'openworkgraph.ai-implementation-proposal.v1'
MAX_BYTES = 8 * 1024 * 1024
MAX_BUNDLES = 8
MAX_EVENTS = 4000
ID = re.compile(r'execution:[a-f0-9]{16}\Z')
EVENT = re.compile(r'event:[a-f0-9]{16}\Z')
ENVIRONMENTS = {'cloud_model', 'local_model', 'hybrid', 'deterministic', 'human'}
BASES = {'observed', 'inferred', 'proposed', 'unknown'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def _read(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    _require(len(data) <= MAX_BYTES, 'input exceeds byte limit')
    result = json.loads(data, object_pairs_hook=_unique_object)
    _json(result)  # Reject non-finite numbers.
    return result


def _text(value, label, maximum=2000):
    _require(isinstance(value, str) and 0 < len(value.strip()) <= maximum and len(value) <= maximum, f'invalid {label}')
    _require(not any(ord(c) < 32 and c not in '\n\t' for c in value), f'invalid {label} control character')
    return value


def _list(value, label, maximum, minimum=0):
    _require(isinstance(value, list) and minimum <= len(value) <= maximum, f'invalid {label} count')
    return value


def _fields(value, required):
    _require(isinstance(value, dict) and set(value) == set(required.split()), 'unexpected or missing fields')


def prepare(sources, *, max_bytes=MAX_BYTES):
    """Keep every selected canonical event, or refuse the budget. Never silently sample."""
    _list(sources, 'source bundle', MAX_BUNDLES, 1)
    _require(type(max_bytes) is int and 1024 <= max_bytes <= MAX_BYTES, 'invalid byte budget')
    bundles, seen, gaps, count = [], set(), [], 0
    for source in sources:
        _require(isinstance(source, dict) and source.get('format') == SOURCE and source.get('canonical_evidence_included') is True, 'expected local workflow evidence with canonical events')
        digest = _digest(source)
        _require(digest not in seen, 'duplicate source bundle')
        seen.add(digest)
        prefix = f'b{len(bundles) + 1}'
        provenance = source.get('provenance')
        _require(isinstance(provenance, dict) and type(provenance.get('source_limit_reached')) is bool, 'missing source coverage')
        canonical = _list(source.get('canonical_evidence'), 'canonical executions', 25, 1)
        executions = _list(source.get('executions'), 'execution summaries', 25, 1)
        summaries = {}
        for entry in executions:
            _require(isinstance(entry, dict), 'invalid execution summary')
            eid = entry.get('execution_id')
            _require(isinstance(eid, str) and ID.fullmatch(eid) and eid not in summaries, 'invalid or duplicate execution ID')
            summaries[eid] = entry
        _require(len(summaries) == len(canonical), 'execution summary and canonical population differ')
        runs, run_ids = [], set()
        if provenance['source_limit_reached']:
            gaps.append({'scope': prefix, 'kind': 'source_limit_reached'})
        for run in canonical:
            _require(isinstance(run, dict), 'invalid canonical execution')
            eid = run.get('execution_id')
            _require(isinstance(eid, str) and eid in summaries and eid not in run_ids, 'unknown or duplicate canonical execution')
            run_ids.add(eid)
            ref = f'{prefix}/{eid}'
            events = _list(run.get('events'), 'canonical events', 160)
            observed, returned = run.get('observed_event_count'), run.get('returned_event_count')
            _require(type(observed) is int and type(returned) is int and observed >= returned == len(events), 'inconsistent event coverage')
            _require(type(run.get('events_truncated')) is bool and run['events_truncated'] == (observed > returned), 'inconsistent truncation')
            if run['events_truncated']:
                gaps.append({'scope': ref, 'kind': 'truncated_execution'})
            if not events:
                gaps.append({'scope': ref, 'kind': 'empty_execution'})
            if run.get('event_membership_verified') is not True:
                gaps.append({'scope': ref, 'kind': 'unverified_membership'})
            event_refs, rows = set(), []
            for event in events:
                _require(isinstance(event, dict), 'invalid event')
                event_ref = event.get('event_ref')
                _require(isinstance(event_ref, str) and EVENT.fullmatch(event_ref) and event_ref not in event_refs, 'invalid or duplicate event reference')
                event_refs.add(event_ref)
                data = {key: event[key] for key in ('observed_at', 'source', 'app', 'window_title', 'event_type', 'duration_seconds', 'metadata') if key in event}
                rows.append({'ref': f'{ref}/{event_ref}', 'untrusted_observed_data': data})
            count += len(rows)
            _require(count <= MAX_EVENTS, 'too many events; narrow selected evidence')
            summary = summaries[eid]
            runs.append({'ref': ref, 'observed_event_count': observed, 'events': rows,
                         'events_truncated': run['events_truncated'], 'event_membership_verified': run.get('event_membership_verified') is True,
                         'trace_lookup': run.get('trace_lookup'),
                         'descriptive_summary': {k: summary[k] for k in ('actor_kind', 'started_at', 'ended_at', 'duration_seconds', 'outcome_status', 'outcome_basis') if k in summary}})
        bundles.append({'ref': prefix, 'source_sha256': digest, 'requested_window': provenance.get('requested_window'), 'runs': runs})
    packet = {'format': PACKET, 'bundles': bundles, 'coverage_gaps': gaps,
              'event_count': count, 'external_ai_interpretation_required': True,
              'source_authenticity_verified': False, 'execution_authorized': False,
              'observations_are_policy': False, 'cross_bundle_membership_verified': False}
    packet['packet_sha256'] = _digest(packet)
    _require(len(_json(packet)) <= max_bytes, 'packet exceeds byte budget; select narrower evidence; no events dropped')
    return packet


def review(sources, proposal):
    """Validate exact references against the original inputs, not semantic correctness."""
    packet = prepare(sources)
    _fields(proposal, 'format packet_sha256 author_model title outcome steps exceptions questions execution_options approval_requirements')
    _require(proposal['format'] == PROPOSAL and proposal['packet_sha256'] == packet['packet_sha256'], 'proposal refers to different evidence packet')
    _text(proposal['author_model'], 'model', 120)
    _text(proposal['title'], 'title', 240)
    runs = {run['ref']: run for bundle in packet['bundles'] for run in bundle['runs']}
    known = {event['ref'] for run in runs.values() for event in run['events']}
    cited = set()

    def references(value, minimum=0):
        refs = _list(value, 'evidence references', 30, minimum)
        _require(all(isinstance(ref, str) and ref in known for ref in refs), 'citation not found in exact supplied canonical events')
        _require(len(refs) == len(set(refs)), 'duplicate citation')
        cited.update(refs)

    def claim(value):
        _fields(value, 'text basis evidence_refs')
        _text(value['text'], 'claim')
        basis = value['basis']
        _require(isinstance(basis, str) and basis in BASES, 'invalid claim basis')
        references(value['evidence_refs'], int(basis in {'observed', 'inferred'}))

    claim(proposal['outcome'])
    for key, minimum in [('steps', 1), ('exceptions', 0)]:
        for entry in _list(proposal[key], key, 100, minimum):
            claim(entry)
    for key in ('questions', 'approval_requirements'):
        for entry in _list(proposal[key], key, 100):
            _text(entry, key)
    _require(not packet['coverage_gaps'] or proposal['questions'], 'coverage gaps require explicit unresolved questions')
    options = _list(proposal['execution_options'], 'execution options', 10, 1)
    for option in options:
        _fields(option, 'environment description required_tools evaluation evidence_refs')
        env = option['environment']
        _require(isinstance(env, str) and env in ENVIRONMENTS, 'invalid execution environment')
        _text(option['description'], 'execution option')
        _text(option['evaluation'], 'evaluation plan')
        for tool in _list(option['required_tools'], 'required tools', 30):
            _text(tool, 'required tool', 240)
        references(option['evidence_refs'])
    _require(bool(cited), 'proposal must cite at least one canonical event')
    return {'format': 'openworkgraph.ai-implementation-review.v1', 'status': 'structural_review_only',
            'packet_sha256': packet['packet_sha256'], 'proposal_sha256': _digest(proposal),
            'cited_event_count': len(cited), 'selected_event_count': packet['event_count'],
            'runs_without_citations': [ref for ref in runs if not any(c.startswith(ref + '/') for c in cited)],
            'coverage_gaps': packet['coverage_gaps'], 'unresolved_question_count': len(proposal['questions']),
            'environments': sorted({o['environment'] for o in options}), 'human_review_required': True,
            'semantic_correctness_verified': False, 'source_authenticity_verified': False,
            'deployment_verified': False, 'execution_authorized': False, 'spending_authorized': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'review'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--source', action='append', required=True, help='Explicitly selected local workflow evidence JSON; repeatable')
        cmd.add_argument('--out', required=True, help='New local JSON file (never overwrites)')
        if name == 'prepare':
            cmd.add_argument('--max-bytes', type=int, default=MAX_BYTES)
        else:
            cmd.add_argument('--proposal', required=True)
    args = parser.parse_args(argv)
    try:
        _require(len(args.source) <= MAX_BUNDLES, 'too many source files')
        sources = [_read(path) for path in args.source]
        result = prepare(sources, max_bytes=args.max_bytes) if args.command == 'prepare' else review(sources, _read(args.proposal))
        data = _json(result)
        with Path(args.out).open('xb') as stream:
            stream.write(data)
            stream.write(b'\n')
    except (ValueError, OSError, UnicodeError, RecursionError) as exc:
        print(f'OWG implementation review refused: {type(exc).__name__}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
