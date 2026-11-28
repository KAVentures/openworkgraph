from copy import deepcopy
from itertools import product
import json
from hashlib import sha256

import pytest
from shared import native_shadow_roster as roster
from shared import native_shadow_trace_review as trace_review
from shared.joint_shadow_source_review import review
from tests.test_native_shadow_trace_review import run, trace
from tests.test_project_source_cohort import fixtures


def payload(native_modes, source_modes):
    m = {'format': roster.PACKAGE, 'mode': 'shadow_only', 'packet_sha256':'a'*64,
         'proposal_sha256':'b'*64,'draft_sha256':'c'*64,
         **dict.fromkeys(('execution_authorized','deployment_verified','client_permissions_verified','semantic_correctness_verified'),False)}
    m['package_sha256'] = roster.digest(m)
    plan = roster.freeze(m,m['package_sha256'],[{'case_id':f'case_{i}','expected_framework':'claude_code',
        'reviewed_tool_labels':['Read']} for i in range(len(native_modes))],confirmed=True)
    assignments=roster.worklist(plan,plan['plan_sha256']); runs=[]
    for i,mode in enumerate(native_modes):
        rid=i+1
        if mode!='unassigned': assignments[i]['root_execution_id']=f'execution:{rid:016x}'
        if mode in ('clean','deviation','incomplete'):
            runs.append(run(rid,states=[('tool_call','success','Write' if mode=='deviation' else 'Read')],finish=mode!='incomplete'))
    t=trace(*runs)
    src,basis,checked=fixtures(source_modes)
    return {'plan':plan,'plan_sha256':plan['plan_sha256'],'trace':t,'trace_sha256':trace_review.digest(t),
        'assignments':assignments,'source_cohort':src},basis,checked


def call(p,b,c):
    return review('idea','project',p,options=lambda *a:b,reconcile=lambda i,j,raw:c[sha256(raw).hexdigest()])


def test_two_dimensions_never_collapsed():
    expected_native={'unassigned':'not_assigned','missing':'root_not_returned','clean':'no_deviation_in_returned_events',
                     'deviation':'observed_deviation_or_failure','incomplete':'incomplete_observation'}
    expected_source={'missing':'missing_document','unknown':'historical_operation_not_established',
                     'verified':'historical_operation_reported_verified','stale':'historical_operation_reported_verified'}
    for nm,sm in product(expected_native,expected_source):
        p,b,c=payload([nm,'clean'],[sm,'missing']);r=call(p,b,c)
        assert r['case_count']==2 and r['cases'][0]['native_observation_status']==expected_native[nm]
        assert r['cases'][0]['historical_source_status']==expected_source[sm]
        assert r['cases'][0]['source_reference_needs_review']==(None if sm=='missing' else sm=='stale')
        assert sum(r['native_status_counts'].values())==sum(r['historical_source_status_counts'].values())==2
        assert all(r[k] is False for k in ('business_outcomes_verified','execution_authorized','case_association_verified','skill_invocation_verified','source_read_performed','package_to_project_association_verified'))
        assert 'owg:r:' not in json.dumps(r) and 'PRIVATE' not in json.dumps(r)
        assert r['review_sha256']==roster.digest({k:v for k,v in r.items() if k!='review_sha256'})


def test_exact_population_before_local_source_review():
    p,b,c=payload(['clean','missing'],['verified','missing'])
    for mode in ('missing','extra','duplicate','changed'):
        q=deepcopy(p);rows=q['source_cohort']['cases']
        if mode=='missing':rows.pop()
        elif mode=='extra':rows.append({**rows[0],'case_id':'case_extra'})
        elif mode=='duplicate':rows[1]['case_id']=rows[0]['case_id']
        else:rows[1]['case_id']='case_other'
        def forbidden(*args): raise AssertionError('local source review must not be called')
        with pytest.raises(ValueError):review('idea','project',q,options=forbidden,reconcile=forbidden)


def test_pin_and_original_receipts_are_rechecked():
    p,b,c=payload(['clean','deviation'],['verified','unknown'])
    q=deepcopy(p);q['trace_sha256']='f'*64
    with pytest.raises(ValueError,match='Exact trace pin'):call(q,b,c)
    q=deepcopy(p);q['source_cohort']['documents'][0]+=' '
    with pytest.raises(ValueError,match='Missing or reused'):call(q,b,c)
    q=deepcopy(p);q['source_cohort']['cases'].reverse()
    r=call(q,b,c)
    assert [row['case_id'] for row in r['cases']]==['case_0','case_1']
    assert r['cases'][0]['historical_source_status']=='historical_operation_reported_verified'
    assert r['cases'][1]['historical_source_status']=='historical_operation_not_established'
