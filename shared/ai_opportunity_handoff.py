"""Review a portable AI proposal before preparing an unassessed pipeline idea."""
import hashlib
import json
import re

HEX = re.compile(r'[a-f0-9]{64}\Z')
FORMAT = 'openworkgraph.ai-opportunity-handoff.v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def human_text(value, limit, required=False):
    require(isinstance(value, str) and len(value) <= limit and
            (not required or bool(value.strip())) and
            not any(ord(c) < 32 and c not in '\n\t' for c in value), 'Invalid human-entered idea text')
    return value.strip()


def prepare(payload, *, reviewer=None):
    """Recompute structural review; never accept an externally supplied receipt."""
    require(isinstance(payload, dict) and set(payload) == {'sources', 'proposal', 'title', 'description'},
            'Supply sources, proposal and human-entered idea title/description')
    title = human_text(payload['title'], 200, True)
    description = human_text(payload['description'], 2000)
    if reviewer is None:
        from .ai_implementation_review import review as reviewer
    review = reviewer(payload['sources'], payload['proposal'])
    require(isinstance(review, dict) and review.get('format') == 'openworkgraph.ai-implementation-review.v1' and
            review.get('status') == 'structural_review_only' and review.get('human_review_required') is True and
            all(review.get(k) is False for k in ('semantic_correctness_verified', 'source_authenticity_verified',
                'deployment_verified', 'execution_authorized', 'spending_authorized')), 'Unexpected structural review')
    packet_pin, proposal_pin = review.get('packet_sha256'), review.get('proposal_sha256')
    require(isinstance(packet_pin, str) and HEX.fullmatch(packet_pin) and isinstance(proposal_pin, str) and
            HEX.fullmatch(proposal_pin) and payload['proposal'].get('packet_sha256') == packet_pin and
            digest(payload['proposal']) == proposal_pin, 'Review pin mismatch')
    review_pin = digest(review)
    idea = {'title': title, 'description': description,
            'source': {'kind': 'manual', 'reference': f'ai-proposal:{packet_pin}:{proposal_pin}'}}
    lineage = {'format': FORMAT, 'packet_sha256': packet_pin, 'proposal_sha256': proposal_pin,
               'structural_review_sha256': review_pin, 'structural_review_only': True,
               'semantic_correctness_verified': False, 'source_authenticity_verified': False,
               'execution_authorized': False}
    body = {'format': FORMAT, 'idea': idea, 'lineage': lineage, 'review': review,
            'state_on_create': 'idea', 'assessment_on_create': None,
            'human_review_required': True, 'execution_authorized': False,
            'interpretation': 'Creating an idea records a human-selected candidate only. No policy, source authenticity, implementation, assessment, savings, deployment or business action is approved.'}
    return {**body, 'preview_sha256': digest(body)}


def confirm(payload, *, reviewer=None):
    require(isinstance(payload, dict) and set(payload) == {'draft', 'expected_preview_sha256', 'confirm_create_idea'}
            and payload['confirm_create_idea'] is True, 'Explicit idea creation confirmation required')
    preview = prepare(payload['draft'], reviewer=reviewer)
    require(payload['expected_preview_sha256'] == preview['preview_sha256'], 'Preview changed; review again')
    return preview['idea'], {**preview['lineage'], 'preview_sha256': preview['preview_sha256']}
