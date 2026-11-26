"""Exact selected-process prerequisite basis over the authorized graph snapshot."""
from .process_dependencies import content_digest, impact


def focused_basis(organization_id, graph, declarations, focus):
    """Scope readiness to a root and every declared prerequisite.

    Do not inherit global all_pins_match_latest: an unrelated consumer may
    hold a stale incoming link to a perfectly current prerequisite.
    """
    reach=impact(graph,focus)
    distances={focus:0,**{r['workflow_id']:r['distance'] for r in reach['prerequisites']}}
    ids=set(distances)
    nodes={r['workflow_id']:r for r in graph['nodes']}
    work={r['workflow_id']:r for r in graph['reassessment_worklist']['processes']}
    entries=[]
    for wid in graph['prerequisite_first_order']:
        if wid not in ids:
            continue
        node=nodes[wid];row=work[wid];saved=declarations.get(wid)
        entries.append({
            'workflow_id':wid,'distance_from_root':distances[wid],
            'current_model':{k:node.get(k) for k in (
                'revision','content_sha256','available','current','state','independent_model_review')},
            'declaration':({'revision':saved['revision'],'content_sha256':saved['content_sha256'],
                'root_process':saved['dependencies']['root_process']} if saved else None),
            'status':row['status'],'direct_findings':row['direct_findings'],
            'stale_prerequisite_links':row['stale_prerequisite_links'],
        })
    edges=[{k:edge[k] for k in ('consumer','prerequisite','link_id','relationship',
        'consumer_process','prerequisite_process','declaration_revision','declaration_sha256')}
        for edge in graph['edges'] if edge['consumer'] in ids]
    payload={'format':'openworkgraph.focused-dependency-build-basis.v1',
        'organization_id':organization_id,'root_workflow_id':focus,
        'processes':entries,'edges':edges}
    ready=all(row['status']=='current_declared_basis' for row in entries)
    return {**payload,'basis_sha256':content_digest(payload),
        'status':'current_declared_basis' if ready else 'needs_reassessment',
        'process_count':len(entries),'prerequisite_count':len(entries)-1,
        'review_needed_count':sum(row['status']!='current_declared_basis' for row in entries),
        'all_declared_prerequisites_current':ready,
        'complete_saved_graph_scan':True,
        'relationship_basis':'human_declared_not_independently_verified',
        'business_dependency_completeness_verified':False,
        'execution_authorized':False,'model_reviews_propagated':False,
        'interpretation':'A minimized exact snapshot of the selected declared closure, not a signed approval, executable subprocess graph or guarantee of current future state.'}
