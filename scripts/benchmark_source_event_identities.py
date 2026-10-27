"""Synthetic, isolated source-ID CSV acceptance; no provider or enterprise SLA claim.

python scripts/benchmark_source_event_identities.py --events 100000
Imports four declared sources with equal-payload pairs, retries, appends distinct
IDs with identical payloads, refuses an out-of-window conflict atomically, and
exports/reimports against an independent row oracle. Uses only temporary data.
"""
import argparse
import csv
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import event_log_workspace as workspace
from server import event_log_quality as quality


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events',type=int,default=100000)
    args=parser.parse_args();n=args.events
    if not 8<=n<=1000000 or n%8:parser.error('events must be a multiple of eight from 8 to 1000000')
    import duckdb
    times={}
    def timed(name,operation):
        start=time.monotonic();result=operation();times[name]=round(time.monotonic()-start,3);return result
    mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time','resource_column':'who',
             'event_id_column':'event','source_namespace_column':'source','case_namespace':'common'}
    with tempfile.TemporaryDirectory(prefix='owg-source-id-oracle-') as folder:
        workspace.DATA_DIR=Path(folder)
        def write(filename,start,end):
            path=Path(folder)/filename
            with path.open('w',newline='') as handle:
                writer=csv.writer(handle);writer.writerow(['event','source','case','activity','time','who'])
                for i in range(start,end):writer.writerow(['e'+str(i//4),'ns'+str(i%4),'c'+str((i%n)//8),'Open','2026-01-01T00:00:00Z','ann'])
            return path
        first=write('first.csv',0,n);new=write('new.csv',n,n+n//2)
        with first.open('rb') as handle:
            made=timed('import',lambda:workspace.import_log(handle,name='Synthetic identity oracle',source_format='csv',mapping=mapping))
        log=made['log_id'];assert made['events']==n and made['quality'] is not None
        with first.open('rb') as handle:
            retry=timed('retry',lambda:workspace.append_log(log,handle,source_format='csv',mapping=mapping))
        assert retry['added_events']==0 and retry['duplicate_events_skipped']==n
        with new.open('rb') as handle:
            added=timed('append_distinct_equal_payload_ids',lambda:workspace.append_log(log,handle,source_format='csv',mapping=mapping))
        assert added['added_events']==n//2 and added['duplicate_events_skipped']==0
        conflict=b'event,source,case,activity,time,who\ne0,ns0,c0,Open,2020-01-01T00:00:00Z,ann\nnew,ns0,new,Open,2026-01-01T00:00:00Z,ann\n'
        def refuse():
            try:workspace.append_log(log,BytesIO(conflict),source_format='csv',mapping=mapping)
            except workspace.EventLogWorkspaceError:pass
            else:raise AssertionError('Conflicting source ID accepted')
        timed('refuse_conflicting_batch',refuse)
        assert workspace.summary(log)['events']==n+n//2 and len(workspace.append_history(log)['appends'])==2
        export=Path(folder)/'roundtrip.csv'
        with export.open('w',newline='') as handle:timed('export',lambda:workspace.export_log(log,'sourcecsv',handle,'source_case'))
        seen=set()
        with export.open(newline='') as handle:
            for row in csv.DictReader(handle):
                ns=int(row['source_namespace'][2:]);i=int(row['source_event_id'][1:])*4+ns
                assert 0<=i<n+n//2 and i not in seen;seen.add(i)
                assert row['case_id']=='c'+str((i%n)//8) and row['case_namespace']=='common'
                assert row['activity']=='Open' and row['timestamp']=='2026-01-01T00:00:00Z' and row['resource']=='ann' and json.loads(row['attributes'])=={}
        assert len(seen)==n+n//2
        roundtrip={'case_column':'case_id','activity_column':'activity','timestamp_column':'timestamp','resource_column':'resource',
            'event_id_column':'source_event_id','source_namespace_column':'source_namespace','case_namespace_column':'case_namespace','attributes_json_column':'attributes'}
        with export.open('rb') as handle:
            copy=timed('reimport',lambda:workspace.import_log(handle,name='Roundtrip oracle',source_format='csv',mapping=roundtrip))
        assert copy['events']==n+n//2 and copy['objects']==n//8 and copy['quality'] is not None
        a=quality.quality(log)['current']['source_identity_map_sha256'];b=quality.quality(copy['log_id'])['current']['source_identity_map_sha256'];assert a==b
        conn=workspace._connect()
        try:settings=dict(conn.execute("SELECT name,value FROM duckdb_settings() WHERE name IN ('memory_limit','threads')").fetchall())
        finally:conn.close()
        peak=None
        try:
            import resource
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
        except ImportError:pass
        print(json.dumps({'fixture':'owned_synthetic_four_declared_sources_equal_payload_pairs','initial_events':n,'retained_events':len(seen),'cases':n//8,'source_namespaces':4,'timings_seconds':times,'roundtrip_key_map_sha256':a,'peak_process_rss_mib':peak,'platform':platform.platform(),'python':platform.python_version(),'duckdb':duckdb.__version__,'duckdb_settings':settings,'logical_cpus':os.cpu_count(),'enterprise_sla':False,'provider_identity_verified':False,'history_completeness':'unknown'},sort_keys=True))

if __name__=='__main__':main()
