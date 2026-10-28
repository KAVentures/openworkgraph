"""Independent two-year scheduled-clock oracle, with no latency/SLA claim."""
import argparse,json,math,sys,tempfile,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
try:
    import resource
except ImportError:
    resource=None
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import event_log_workspace as workspace,event_log_waiting as waiting


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events',type=int,default=100000)
    parser.add_argument('--mode',choices=['lifecycle','end_attribute'],default='lifecycle')
    args=parser.parse_args();per_case=8 if args.mode=='lifecycle' else 4
    if not per_case<=args.events<=1000000 or args.events%per_case:parser.error('use whole four-instance cases, up to one million events')
    cases=args.events//per_case;origin=datetime(2024,1,1,tzinfo=timezone.utc);holidays={3,100}
    cal={'timezone':'UTC','hours':{d:[['09:00','17:00']] for d in ['mon','tue','wed','thu','fri']},
         'holidays':[(origin+timedelta(days=d)).date().isoformat() for d in sorted(holidays)]}
    eligible=sum(1 for c in range(cases) if (c%730)%7<5 and c%730 not in holidays)
    with tempfile.TemporaryDirectory(prefix='owg-working-calendar-bench-') as folder:
        workspace.DATA_DIR=Path(folder)/'data';workspace.DATA_DIR.mkdir(mode=0o700)
        source=Path(folder)/'source.csv';attribute='lifecycle:transition' if args.mode=='lifecycle' else 'finished'
        with source.open('w',encoding='utf-8') as stream:
            stream.write('case,activity,time,'+attribute+'\n')
            for c in range(cases):
                day=origin+timedelta(days=c%730)
                for label,a,b in [('Long',0,10),('Short',1,2),('Next',20,21),('Last',22,23)]:
                    lo=(day+timedelta(hours=a)).isoformat();hi=(day+timedelta(hours=b)).isoformat()
                    if args.mode=='lifecycle':stream.write(f'c{c},{label},{lo},start\nc{c},{label},{hi},complete\n')
                    else:stream.write(f'c{c},{label},{lo},{hi}\n')
        started=time.monotonic()
        with source.open('rb') as stream:made=workspace.import_log(stream,name='Owned calendar scale',source_format='csv',mapping={
            'case_column':'case','activity_column':'activity','timestamp_column':'time','attribute_columns':[attribute]})
        imported=time.monotonic()-started;settings={'mode':args.mode,**({'end_attribute':attribute} if args.mode=='end_attribute' else {})}
        started=time.monotonic();report=waiting.waiting_time(made['log_id'],working_calendar=cal,**settings);seconds=time.monotonic()-started
        lens=report['working_calendar'];flow=lens['flow_efficiency']
        assert report['events']==args.events and lens['source_instances']==cases*4
        assert flow['cases']==eligible and flow['cases_with_zero_calendar_span']==cases-eligible
        assert flow['busy_seconds']==eligible*3600 and flow['elapsed_seconds']==eligible*8*3600
        if eligible:assert math.isclose(flow['median'],1/8,rel_tol=1e-12)
        assert lens['instances_crossing_closed_time']==cases*4
        assert lens['summed_instance_seconds_outside_calendar']==(eligible*12+(cases-eligible)*13)*3600
        hot={(r['from'],r['to']):r for r in lens['waiting_hotspots']}
        assert hot[('Long','Next')]['total_waiting_seconds']==eligible*7*3600
        assert hot[('Long','Short')]['total_waiting_seconds']==hot[('Next','Last')]['total_waiting_seconds']==0
        legacy=waiting.waiting_time(made['log_id'],**settings)
        assert {k:v for k,v in report.items() if k!='working_calendar'}==legacy
        assert report['flow_efficiency']['busy_seconds']==cases*12*3600
        peak=None if resource is None else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
        print(json.dumps({'events':args.events,'cases':cases,'mode':args.mode,'oracle_passed':True,
            'eligible_cases':eligible,'utc_opening_windows':lens['utc_opening_windows'],
            'calendar_sha256':lens['calendar_sha256'],'utc_opening_windows_sha256':lens['utc_opening_windows_sha256'],
            'import_seconds':round(imported,3),'calendar_analysis_seconds':round(seconds,3),'peak_rss_mib':peak,
            'interpretation':'synthetic two-year interval oracle, not latency SLA, staffing/labor or real-process accuracy'}))


if __name__=='__main__':main()
