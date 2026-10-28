"""Synthetic overlapping-interval correctness/scale check; no latency SLA."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
try:
    import resource
except ImportError:
    resource=None
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import event_log_waiting as waiting, event_log_workspace as workspace


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events',type=int,default=100000)
    parser.add_argument('--mode',choices=['lifecycle','end_attribute'],default='lifecycle')
    args=parser.parse_args();per_case=8 if args.mode=='lifecycle' else 4
    if args.events<per_case or args.events>1000000 or args.events%per_case:parser.error('events must contain whole four-instance cases, up to one million events')
    cases=args.events//per_case;start=datetime(2024,1,1,tzinfo=timezone.utc)
    spans=[('Long',0,10),('Short',1,2),('Next',20,21),('Last',22,23)]
    with tempfile.TemporaryDirectory(prefix='owg-wait-boundary-bench-') as folder:
        workspace.DATA_DIR=Path(folder)/'data';workspace.DATA_DIR.mkdir(mode=0o700);source=Path(folder)/'source.csv'
        with source.open('w',encoding='utf-8') as out:
            out.write('case,activity,time,'+('lifecycle:transition' if args.mode=='lifecycle' else 'finished')+'\n')
            for c in range(cases):
                for label,lo,hi in spans:
                    begin=(start+timedelta(hours=lo)).isoformat();end=(start+timedelta(hours=hi)).isoformat()
                    if args.mode=='lifecycle':
                        out.write(f'c{c},{label},{begin},start\nc{c},{label},{end},complete\n')
                    else:out.write(f'c{c},{label},{begin},{end}\n')
        before=time.monotonic()
        with source.open('rb') as data:
            made=workspace.import_log(data,name='Synthetic boundary scale',source_format='csv',mapping={
                'case_column':'case','activity_column':'activity','timestamp_column':'time',
                'attribute_columns':['lifecycle:transition' if args.mode=='lifecycle' else 'finished']})
        import_seconds=time.monotonic()-before;before=time.monotonic()
        result=waiting.waiting_time(made['log_id'],mode=args.mode,
            **({'end_attribute':'finished'} if args.mode=='end_attribute' else {}))
        query_seconds=time.monotonic()-before
        assert result['events']==args.events
        assert result['instances']['total']==result['instances']['with_processing_time']==cases*4
        hot={(r['from'],r['to']):r for r in result['waiting_hotspots']}
        assert set(hot)=={('Long','Short'),('Long','Next'),('Next','Last')}
        for pair,hours in [(('Long','Short'),0),(('Long','Next'),10),(('Next','Last'),1)]:
            assert hot[pair]['count']==cases and hot[pair]['median_waiting_seconds']==hours*3600
            assert hot[pair]['total_waiting_seconds']==cases*hours*3600
        flow=result['flow_efficiency']
        assert flow['cases']==cases and flow['busy_seconds']==cases*12*3600 and flow['elapsed_seconds']==cases*23*3600
        assert math.isclose(flow['median'],12/23,rel_tol=1e-12)
        import duckdb
        print(json.dumps({'events':args.events,'cases':cases,'mode':args.mode,'oracle_passed':True,
            'import_seconds':round(import_seconds,3),'waiting_seconds':round(query_seconds,3),
            'peak_rss_mib':None if resource is None else round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024),3),
            'duckdb':duckdb.__version__,'source_bytes':source.stat().st_size,
            'interpretation':'synthetic interval oracle; not a latency SLA, causal handoff or real-work accuracy claim'}))


if __name__=='__main__':main()
