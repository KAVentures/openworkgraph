from copy import deepcopy
import json
from random import Random
import pytest
from shared.ai_context_compaction import compact, expand, digest, canonical, main


def packet(values):
    runs = []
    for i, payloads in enumerate(values):
        ref = 'b1/execution:' + f'{i:016x}'
        runs.append({'ref':ref,'events':[{'ref':ref+'/event:'+f'{j:016x}','untrusted_observed_data':p} for j,p in enumerate(payloads)],
                     'observed_event_count':len(payloads)+1,'events_truncated':True,'event_membership_verified':False})
    p = {'format':'openworkgraph.ai-implementation-evidence.v1','bundles':[{'ref':'b1','runs':runs}],
         'event_count':sum(map(len,values)),'execution_authorized':False,'observations_are_policy':False,
         'coverage_gaps':[{'kind':'truncated_execution'}]}
    p['packet_sha256'] = digest(p)
    return p


def test_randomized_lossless_occurrences():
    rng = Random(1663)
    for _ in range(300):
        pool = [{'value':rng.randrange(5),'text':'PRIVATE ä😀'*rng.randrange(1,8)} for _ in range(5)]
        values = [rng.choices(pool,k=rng.randrange(0,8)) for _ in range(rng.randrange(1,6))]
        p = packet(values); original = deepcopy(p)
        c,r = compact(p,p['packet_sha256'])
        assert p == original and canonical(expand(c,p['packet_sha256'])) == canonical(p)
        assert r['distinct_exact_payloads'] == len({canonical(v) for row in values for v in row})
        assert r['event_occurrences'] == sum(map(len,values))
        assert r['canonical_bytes_saved'] == len(canonical(p))-len(canonical(c))
        assert 'PRIVATE' not in json.dumps(r) and not r['token_savings_measured']


def test_duplicate_occurrences_and_different_payloads():
    a={'text':'x'*2000}; b={'text':'different'}
    p=packet([[a,a,b],[a,b]])
    c,r=compact(p,p['packet_sha256'])
    assert r['event_occurrences']==5 and r['distinct_exact_payloads']==2 and r['canonical_bytes_saved']>0
    assert len({row['ref'] for run in c['packet_skeleton']['bundles'][0]['runs'] for row in run['events']})==5
    assert expand(c,p['packet_sha256'])==p


def repin(c):
    c['compact_sha256']=digest({k:v for k,v in c.items() if k!='compact_sha256'})
    return c


def test_tampering_and_invalid_scope():
    p=packet([[{'a':1}],[{'a':2}]])
    pin=p['packet_sha256'];c,_=compact(p,pin)
    for mode in ('payload','unused','missing','scope','coverage','reorder'):
        bad=deepcopy(c);runs=bad['packet_skeleton']['bundles'][0]['runs']; table=bad['payloads']
        if mode=='payload': next(iter(table.values()))['a']=9
        elif mode=='unused': table[digest({'z':1})]={'z':1}
        elif mode=='missing': table.pop(next(iter(table)))
        elif mode=='scope': runs[0]['events'][0]['ref']=runs[1]['events'][0]['ref']
        elif mode=='coverage': runs[0]['observed_event_count']=3
        else: runs.reverse()
        with pytest.raises(ValueError): expand(repin(bad),pin)
    with pytest.raises(ValueError,match='pin mismatch'): compact(p,'f'*64)


def test_cli_and_expansion_budget(tmp_path,monkeypatch):
    p=packet([[{'a':'x'*2000}]*10]); pin=p['packet_sha256']
    c,_=compact(p,pin)
    import shared.ai_context_compaction as m
    monkeypatch.setattr(m,'MAX_BYTES',len(canonical(c))+100)
    with pytest.raises(ValueError,match='Expanded byte budget'): expand(c,pin)
    monkeypatch.setattr(m,'MAX_BYTES',16*1024*1024)
    (tmp_path/'p').write_bytes(canonical(p))
    args=['--packet-sha256',pin,'--confirm-local-context']
    assert main(['compact',*args,'--input',str(tmp_path/'p'),'--output',str(tmp_path/'c')])==0
    assert main(['expand',*args,'--input',str(tmp_path/'c'),'--output',str(tmp_path/'restored')])==0
    assert (tmp_path/'restored').read_bytes()==canonical(p)
    with pytest.raises(SystemExit) as exc: main(['expand',*args,'--input',str(tmp_path/'c'),'--output',str(tmp_path/'restored')])
    assert exc.value.code==2
