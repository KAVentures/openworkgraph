"""Owned synthetic OCEL assertions: exact tuple, paging, append and export oracles.

python scripts/benchmark_object_relationships.py --relationships 100000
No provider, model, observation or network access. Uses only a temporary store.
Timings describe this fixture and machine, not a service-level guarantee.
"""
import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import event_log_workspace as workspace,event_log_quality as quality
from server import event_log_object_relationships as relationships
from shared import event_log_formats as formats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--relationships',type=int,default=100000)
    args=parser.parse_args();n=args.relationships
    if n%100 or not 10000<=n<=1000000:parser.error('Use a multiple of 100 from 10000 to 1000000')
    objects=n//100;times={}
    def timed(name,fn):
        start=time.monotonic();result=fn();times[name]=round(time.monotonic()-start,3);return result
    def target(source,index):return (source+index)%objects
    def qualifier(index):return ['', ' ', 'contains'][index%3]
    with tempfile.TemporaryDirectory(prefix='owg-o2o-scale-') as folder:
        root=Path(folder);workspace.DATA_DIR=root
        source=root/'source.xmlocel'
        with source.open('w') as out:
            out.write('<log><object-types/><event-types/><objects>')
            for i in range(objects):
                out.write(f'<object id="o{i}" type="case"><attributes/><objects>')
                for j in range(100):out.write(f'<relationship object-id="o{target(i,j)}" qualifier="{qualifier(j)}"/>')
                out.write('</objects></object>')
            out.write('</objects><events><event id="e" type="Open" time="2026-01-01T00:00:00Z"><attributes/><objects><relationship object-id="o0" qualifier=""/></objects></event></events></log>')
        with source.open('rb') as handle:made=timed('import',lambda:workspace.import_log(handle,name='Owned O2O oracle',source_format='ocelxml'))
        log=made['log_id'];assert made['events']==1 and made['objects']==objects and made['object_relationships']['retained_relationships']==n
        with source.open('rb') as handle:retry=timed('retry',lambda:workspace.append_log(log,handle,source_format='ocelxml'))
        assert retry['added_events']==0 and retry['added_object_relationships']==0
        # Direct unobserved endpoints stay queryable, without inventing events.
        result=timed('page_unobserved',lambda:relationships.query(log,{'object_id':'o1','direction':'outgoing','limit':50}))
        assert result['matching_relationships']==100 and result['selected_object']['observed_events']==0 and result['has_more']
        second=relationships.query(log,{'object_id':'o1','direction':'outgoing','limit':50,'cursor':result['next_cursor']})
        assert len(second['rows'])==50 and not second['has_more']
        actual={(r['source_id'],r['target_id'],r['qualifier']) for r in result['rows']+second['rows']}
        assert actual=={('o1','o'+str(target(1,j)),qualifier(j)) for j in range(100)}
        before=quality.quality(log)['current']['object_relationships']['relationships_sha256']
        changed=('<log><object-types/><event-types/><objects><object id="o0" type="case"><attributes/><objects>'
                 '<relationship object-id="o0" qualifier="new-role"/></objects></object></objects><events>'
                 '<event id="e" type="Open" time="2026-01-01T00:00:00Z"><attributes/><objects>'
                 '<relationship object-id="o0" qualifier=""/></objects></event></events></log>').encode()
        if n==1000000:
            def refuse():
                try:workspace.append_log(log,BytesIO(changed),source_format='ocelxml')
                except workspace.EventLogWorkspaceError:pass
                else:raise AssertionError('Combined retained relationship cap was not enforced')
            timed('refuse_over_limit_append',refuse)
            assert quality.quality(log)['current']['object_relationships']['relationships_sha256']==before
            added=0
        else:
            append=timed('append_zero_new_events',lambda:workspace.append_log(log,BytesIO(changed),source_format='ocelxml'))
            assert append['added_events']==0 and append['added_object_relationships']==1;added=1
        expected=quality.quality(log)['current']['object_relationships']['relationships_sha256']
        for format,suffix,reader in [('ocel','jsonocel',formats.read_ocel_json),('ocelxml','xmlocel',formats.read_ocel_xml)]:
            export=root/('roundtrip.'+suffix)
            with export.open('w') as out:timed('export_'+format,lambda:workspace.export_log(log,format,out))
            # Independent formula checks every exported tuple, not just a count/hash.
            total=0;seen=set();events=0
            with export.open('rb') as handle:
                for record in reader(handle):
                    if isinstance(record,formats.EventRecord):events+=1;continue
                    i=int(record.object_id[1:]);assert i not in seen;seen.add(i)
                    expect={('o'+str(target(i,j)),qualifier(j)) for j in range(100)}
                    if added and i==0:expect.add(('o0','new-role'))
                    assert len(record.relationships)==len(expect) and set(record.relationships)==expect
                    total+=len(record.relationships)
            assert total==n+added and len(seen)==objects and events==1
            with export.open('rb') as handle:copy=timed('reimport_'+format,lambda:workspace.import_log(handle,name='Roundtrip',source_format=format))
            assert copy['events']==1 and copy['objects']==objects
            assert quality.quality(copy['log_id'])['current']['object_relationships']['relationships_sha256']==expected
        conn=workspace._connect()
        try:settings=dict(conn.execute("SELECT name,value FROM duckdb_settings() WHERE name IN ('memory_limit','threads')").fetchall())
        finally:conn.close()
        peak=None
        try:
            import resource
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
        except ImportError:pass
        print(json.dumps({'fixture':'owned_synthetic_direct_assertions_with_unobserved_endpoints','relationships':n+added,
            'objects':objects,'events':1,'timings_seconds':times,'roundtrip_relationships_sha256':expected,
            'peak_process_rss_mib':peak,'platform':platform.platform(),'python':platform.python_version(),
            'duckdb_settings':settings,'logical_cpus':os.cpu_count(),'enterprise_sla':False,
            'provider_identity_verified':False,'history_completeness':'unknown'},sort_keys=True))


if __name__=='__main__':main()
