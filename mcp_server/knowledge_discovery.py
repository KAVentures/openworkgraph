"""Bounded literal navigation over the existing authorized knowledge response."""
import re

FORMAT = 'openworkgraph.workflow-knowledge-discovery.v1'
MAX_SCAN = 100
ID = re.compile(r'[a-zA-Z0-9_-]{1,80}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def request_args(workflow_id, limit, query, summary_only):
    require(isinstance(query, str) and len(query) <= 200 and not any(ord(c) < 32 for c in query), 'Invalid knowledge query')
    terms = query.casefold().split()
    require(len(terms) <= 8 and (not terms or not workflow_id), 'Use a literal query or an exact workflow ID, not both')
    require(type(limit) is int and 1 <= limit <= MAX_SCAN, 'Discovery limit must be 1–100')
    require(isinstance(workflow_id, str) and (not workflow_id or ID.fullmatch(workflow_id)), 'Invalid workflow ID')
    require(type(summary_only) is bool, 'Invalid summary mode')
    return {'workflow_id': workflow_id, 'limit': MAX_SCAN}, terms


def discover(response, terms, limit):
    require(isinstance(response, dict) and response.get('format') == 'openworkgraph.workflow-knowledge.v1'
            and response.get('knowledge_is_observed_evidence') is False
            and response.get('knowledge_is_execution_authorization') is False
            and type(response.get('has_more')) is bool, 'Unexpected knowledge inventory')
    records = response.get('workflows')
    require(isinstance(records, list) and len(records) <= MAX_SCAN and response.get('returned') == len(records), 'Unexpected knowledge inventory size')
    matches, seen = [], set()
    for r in records:
        require(isinstance(r, dict) and isinstance(r.get('workflow_id'), str) and ID.fullmatch(r['workflow_id'])
                and r['workflow_id'] not in seen and isinstance(r.get('title'), str)
                and len(r['title']) <= 240 and type(r.get('revision')) is int and r['revision'] >= 1
                and isinstance(r.get('content_sha256'), str) and HEX.fullmatch(r['content_sha256'])
                and r.get('observed_evidence') is False and r.get('execution_authorization') is False
                and r.get('organization_policy') is False, 'Invalid saved knowledge reference')
        seen.add(r['workflow_id'])
        model = r.get('process_model')
        goal = model.get('goal', '') if isinstance(model, dict) else ''
        procedure = r.get('procedure', '')
        require(isinstance(goal, str) and isinstance(procedure, str), 'Invalid saved knowledge text')
        haystack = '\n'.join((r['workflow_id'], r['title'], goal, procedure)).casefold()
        if all(term in haystack for term in terms):
            matches.append({k:r[k] for k in ('workflow_id','title','revision','content_sha256')} |
                           {'has_process_model':isinstance(model,dict)})
    return {'format':FORMAT, 'candidates':matches[:limit], 'records_scanned':len(records),
            'matches_in_scanned_records':len(matches), 'returned':min(limit,len(matches)),
            'source_inventory_truncated':response['has_more'], 'matches_truncated':len(matches)>limit,
            'search_scope':'literal casefolded terms in returned workflow ID, title, goal and procedure',
            'semantic_relevance_verified':False, 'content_digest_verified':False,
            'applicability_verified':False, 'organization_policy':False, 'execution_authorized':False,
            'interpretation':'Navigation only, not a semantic search or complete catalog scan. No match does not establish absence. Retrieve relevant exact workflow IDs using get_workflow_knowledge without discovery arguments and check the returned revision and digest before reuse.'}
