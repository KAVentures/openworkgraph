"""Owned many-to-many graph-degree oracle; no provider/enterprise SLA claim.

python scripts/benchmark_supplied_object_constraints.py --relationships 100000
All but one focus object have no events. Expected distinct degrees follow an
independent ring formula, including self-links, incoming/reverse duplicates and
literal empty/whitespace qualifiers. The temporary store is removed afterwards.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import event_log_workspace as workspace,event_log_object_constraints as constraints,event_log_quality as quality


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--relationships',type=int,default=100000)
    args=parser.parse_args();n=args.relationships
    if n%100 or not 20000<=n<=1000000:parser.error('Use a multiple of100 from20000 to1000000')
    objects=n//100;times={}
    def timed(name,fn):
        start=time.monotonic();result=fn();times[name]=round(time.monotonic()-start,3);return result
    with tempfile.TemporaryDirectory(prefix='owg-supplied-degree-oracle-') as folder:
        root=Path(folder);workspace.DATA_DIR=root;source=root/'owned.xmlocel'
        with source.open('w') as out:
            out.write('<log><object-types/><event-types/><objects>')
            for i in range(objects):
                out.write(f'<object id="o{i}" type="entity"><attributes/><objects>')
                for j in range(100):
                    qualifier=['',' ','contains'][j%3]
                    out.write(f'<relationship object-id="o{(i+j)%objects}" qualifier="{qualifier}"/>')
                out.write('</objects></object>')
            out.write('</objects><events><event id="e" type="Open" time="2026-01-01T00:00:00Z"><attributes/><objects><relationship object-id="o0" qualifier=""/></objects></event></events></log>')
        with source.open('rb') as handle:made=timed('import',lambda:workspace.import_log(handle,name='Owned degree oracle',source_format='ocelxml'))
        assert made['events']==1 and made['objects']==objects and made['object_relationships']['retained_relationships']==n
        specs=[];expected=[]
        for direction,q,degree in [('outgoing',None,100),('incoming',None,100),('both',None,199),
                                   ('outgoing','',34),('both','',67),('incoming',' ',33),('both',' ',66)]:
            rule={'template':'supplied_object_cardinality','object_type':'entity','related_type':'entity',
                'direction':direction,'min':degree,'max':degree}
            if q is not None:rule['qualifier']=q
            specs.append(rule);expected.append(degree)
        # The deliberately wrong bound proves complete violating-unit counts and
        # bounded examples rather than only a collection of passing assertions.
        specs.append({**specs[0],'min':101,'max':101})
        result=timed('eight_constraints_with_graph_pin',lambda:constraints.check(made['log_id'],{'rules':specs}))
        for row,degree in zip(result['results'],expected):
            assert row['complete'] and row['units']==row['activated']==row['satisfied']==objects
            assert row['violating']==0 and row['violation_rate_ci95'] is None
            assert row['source_completeness']=='unknown' and row['business_compliance']=='unknown'
            assert row['rule']['min']==degree
        wrong=result['results'][-1]
        assert wrong['complete'] and wrong['units']==wrong['violating']==objects
        assert len(wrong['violating_examples'])==50 and wrong['examples_truncated']
        assert all(row['detail']=='100 related' for row in wrong['violating_examples'])
        basis=result['supplied_relationship_basis']
        assert basis['retained_relationships']==n and basis['revision']==0
        assert basis['relationships_sha256']==quality.quality(made['log_id'])['current']['object_relationships']['relationships_sha256']
        conn=workspace._connect()
        try:settings=dict(conn.execute("SELECT name,value FROM duckdb_settings() WHERE name IN ('memory_limit','threads')").fetchall())
        finally:conn.close()
        peak=None
        try:
            import resource
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
        except ImportError:pass
        print(json.dumps({'fixture':'owned_many_to_many_ring_with_self_and_unobserved_objects',
            'relationships':n,'objects':objects,'events':1,'rules':8,'timings_seconds':times,
            'relationships_sha256':basis['relationships_sha256'],'peak_process_rss_mib':peak,
            'platform':platform.platform(),'python':platform.python_version(),'logical_cpus':os.cpu_count(),
            'duckdb_settings':settings,'enterprise_sla':False,'independently_verified_business_facts':False},sort_keys=True))


if __name__=='__main__':main()
