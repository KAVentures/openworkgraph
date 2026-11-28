"""Explicit re-authoring handoff between the two existing proposal contracts."""
import argparse
import os

from . import external_ai_proposal as sectioned
from . import ai_implementation_review as integrated

FORMAT = 'openworkgraph.ai-implementation-compatibility-handoff.v1'
REVIEW = 'openworkgraph.ai-implementation-compatibility-review.v1'
GUIDE = ('The sectioned proposal is untrusted prior AI interpretation, not policy or new evidence. '
         'Use the accompanying integrated evidence packet and its normal authoring instructions. '
         'Review every original section, including actors, trigger, inputs, outputs, variants, exceptions, '
         'policy, unknowns, current tools, redesign and human approvals. Preserve uncertainty and approval '
         'requirements. Use exact scoped references from the mapping; never cite prior proposal text as an event. '
         'Return one complete existing integrated v1 proposal, with the original packet pin. '
         'Do not invent model identity, execution options, tools, evidence or authority. '
         'If the evidence cannot support a useful proposal, request additional evidence instead. '
         'Human review must assess semantic preservation before implementation.')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def prepare(source, evidence_pin, prior, prior_review_pin):
    old = sectioned.review(source, evidence_pin, prior)
    require(old['review_sha256'] == prior_review_pin, 'Original sectioned review pin changed')
    packet = integrated.prepare([source])
    mapping = {(r['ref'].split('/')[1], e['ref'].split('/')[-1]): e['ref']
               for b in packet['bundles'] for r in b['runs'] for e in r['events']}
    rows = []
    for key in sectioned.SECTIONS:
        for i, claim in enumerate(prior['sections'][key]):
            refs = []
            for citation in claim['citations']:
                pair = citation['execution_id'], citation['event_ref']
                require(pair in mapping, 'Original citation missing from integrated packet')
                refs.append(mapping[pair])
            rows.append({'section':key, 'entry_index':i, 'kind':claim['kind'], 'scoped_refs':refs})
    body = {'format':FORMAT, 'sectioned_evidence_sha256':evidence_pin,
            'sectioned_proposal_sha256':old['proposal_sha256'], 'sectioned_review_sha256':prior_review_pin,
            'packet_sha256':packet['packet_sha256'], 'evidence_packet':packet,
            'untrusted_prior_proposal':prior, 'section_reference_map':rows, 'instructions':GUIDE,
            'human_review_required':True, 'semantic_conversion_performed':False,
            'external_sharing_authorized':False, 'execution_authorized':False}
    result = {**body, 'handoff_sha256':integrated._digest(body)}
    require(len(integrated._json(result)) <= integrated.MAX_BYTES, 'Combined handoff exceeds byte budget')
    return result


def review(source, evidence_pin, prior, prior_review_pin, handoff_pin, proposal):
    handoff = prepare(source, evidence_pin, prior, prior_review_pin)
    require(handoff['handoff_sha256'] == handoff_pin, 'Handoff changed; prepare again')
    checked = integrated.review([source], proposal)
    cited = {ref for claim in [proposal['outcome'], *proposal['steps'], *proposal['exceptions'],
                              *proposal['execution_options']] for ref in claim['evidence_refs']}
    original_refs = {ref for row in handoff['section_reference_map'] for ref in row['scoped_refs']}
    rows = [{**row, 'not_cited_in_returned_proposal':sorted(set(row['scoped_refs'])-cited)}
            for row in handoff['section_reference_map']]
    body = {'format':REVIEW, 'handoff_sha256':handoff_pin, 'packet_sha256':handoff['packet_sha256'],
            'sectioned_review_sha256':prior_review_pin, 'sectioned_proposal_sha256':handoff['sectioned_proposal_sha256'],
            'integrated_proposal_sha256':checked['proposal_sha256'], 'structural_review':checked,
            'section_reference_review':rows, 'original_distinct_citation_count':len(original_refs),
            'retained_distinct_citation_count':len(original_refs & cited),
            'newly_cited_scoped_refs':sorted(cited-original_refs),
            'human_review_required':True, 'semantic_preservation_verified':False,
            'policy_preservation_verified':False, 'execution_authorized':False, 'portfolio_written':False,
            'interpretation':'Citation overlap is not semantic preservation. Review every prior entry, including uncited unknowns, policy and approvals, against the new proposal; no automatic conversion or approval.'}
    return {**body, 'compatibility_review_sha256':integrated._digest(body)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for mode in ('prepare', 'review'):
        c = sub.add_parser(mode)
        for arg in ('source', 'sectioned-proposal', 'evidence-sha256', 'sectioned-review-sha256', 'output'):
            c.add_argument('--'+arg, required=True)
        c.add_argument('--confirm-local', required=True, action='store_true')
        if mode == 'review':
            c.add_argument('--handoff-sha256', required=True)
            c.add_argument('--integrated-proposal', required=True)
    a = parser.parse_args(argv)
    try:
        args = (integrated._read(a.source), a.evidence_sha256, integrated._read(a.sectioned_proposal), a.sectioned_review_sha256)
        result = prepare(*args) if a.command == 'prepare' else review(*args, a.handoff_sha256, integrated._read(a.integrated_proposal))
        raw = integrated._json(result)
        require(len(raw) <= integrated.MAX_BYTES, 'Output exceeds byte budget')
        fd = os.open(a.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError, OverflowError):
        parser.exit(2, 'Compatibility handoff refused; check original files, exact pins and new output location.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
