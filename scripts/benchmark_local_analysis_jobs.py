"""Owned synthetic local-job oracle; never uses an existing OWG database or observes work.

Run from a source checkout in its venv:
  python scripts/benchmark_local_analysis_jobs.py --cases 100000
Not an enterprise SLA, CSV import benchmark or real-customer acceptance test.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import event_log_workspace as workspace
from server.event_log_jobs import jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=100000)
    args = parser.parse_args()
    if not 1 <= args.cases <= 500000:
        parser.error('cases must be from1 to500000')
    import duckdb
    source = 'log_' + 'a' * 16
    with tempfile.TemporaryDirectory(prefix='owg-local-job-oracle-') as folder:
        workspace.DATA_DIR = Path(folder)
        with workspace._LOCK:
            conn = workspace._connect()
            try:
                conn.execute('INSERT INTO logs VALUES (?,?,?,?,?,?,?)',
                    [source,'owned synthetic oracle','csv',datetime(2024,1,1),args.cases*10,args.cases,0])
                conn.execute("""INSERT INTO events SELECT ?, 'e'||i::VARCHAR,
                    CASE WHEN i%2=0 THEN 'A' ELSE 'B' END,
                    TIMESTAMP '2024-01-01' + (i%10)*INTERVAL '1 minute', '', '{}'
                    FROM range(?) t(i)""", [source,args.cases*10])
                conn.execute("INSERT INTO objects SELECT ?, 'c'||i::VARCHAR, 'case', '{}' FROM range(?) t(i)", [source,args.cases])
                conn.execute("INSERT INTO event_objects SELECT ?, 'e'||i::VARCHAR, 'c'||(i//10)::VARCHAR, '' FROM range(?) t(i)", [source,args.cases*10])
            finally:
                conn.close()
        spec = {'level':'cases','metrics':[{'kind':'activity_count','name':'a_count','activity':'A'}],
                'measures':[{'fn':'count'},{'fn':'sum','of':'duration_seconds'},
                            {'fn':'p90','of':'duration_seconds'},{'fn':'sum','of':'metric:a_count'}]}
        started = time.monotonic()
        job = jobs.submit('owned-benchmark-session-key',source,spec)['job_id']
        while True:
            status = jobs.status('owned-benchmark-session-key',source,job)
            if status['state'] not in {'queued','running'}:
                break
            time.sleep(.01)
        assert status['state'] == 'succeeded',status
        result = json.loads(jobs.result('owned-benchmark-session-key',source,job))
        elapsed = time.monotonic()-started
        expected = [[args.cases,args.cases*540,540,args.cases*5]]
        assert result['rows'] == expected, (result['rows'],expected)
        assert result['matching'] == {'rows':args.cases,'cases':args.cases}
        assert result['metrics'][0]['cases_without_value'] == 0
        jobs.remove('owned-benchmark-session-key',source,job)
        worker = jobs._worker
        if worker:
            worker.join(timeout=5)
            assert not worker.is_alive()
        rss = None
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss = peak/(1024*1024) if sys.platform=='darwin' else peak/1024
        except ImportError:
            pass
        print(json.dumps({'fixture':'owned_synthetic_sql_seed_not_provider_or_import_acceptance',
            'cases':args.cases,'events':args.cases*10,'event_distribution':'10 events/case, alternating A/B, one-minute intervals',
            'expected_and_actual_rows':expected,'elapsed_seconds':round(elapsed,3),'peak_process_rss_mib':rss,
            'platform':platform.platform(),'machine':platform.machine(),'logical_cpus':os.cpu_count(),
            'python':platform.python_version(),'duckdb':duckdb.__version__,
            'durable':False,'enterprise_sla':False,'source_version_pinned_at_submission':False},sort_keys=True))


if __name__ == '__main__':
    main()
