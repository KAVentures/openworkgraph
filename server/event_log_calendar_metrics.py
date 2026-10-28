"""Optional supplied-calendar clock over recorded intervals; no labor/policy proof."""
from datetime import timedelta, timezone, datetime
import hashlib
import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from .event_log_workspace import EventLogWorkspaceError

MAX_DAYS = 3660
MAX_INSTANCES = 1000000
POLICY = 'earliest_start_latest_end_union.v1'


class WaitingCalendarRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    mode:Literal['auto','lifecycle','end_attribute']='auto'
    lifecycle_attribute:str=Field(default='lifecycle:transition',min_length=1,max_length=120)
    end_attribute:str=Field(default='',max_length=120)
    working_calendar:dict[str,Any] | None=None


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def parse(spec):
    if not isinstance(spec,dict) or set(spec)-{'timezone','hours','holidays'}:
        raise EventLogWorkspaceError('A working calendar has timezone, hours and optional holidays; scenario start is not an analysis setting')
    from .event_log_simulation import parse_calendar
    return parse_calendar(spec)


def measure(conn,calendar):
    """seq2 is the existing case-projected interval table; it is never modified."""
    description=calendar.describe()
    config={k:description[k] for k in ('timezone','hours','holidays')}
    basis={'calendar':config,'boundary_policy':POLICY}
    count,first,last=conn.execute('SELECT count(*),min(st),max(en) FROM seq2').fetchone()
    base={'format':'openworkgraph.supplied-working-calendar-metrics.v1',**basis,
          'calendar_sha256':digest(basis),'source_instances':count,
          'actual_working_hours_verified':False,'business_policy_compliance_verified':False,
          'source_completeness_verified':False,'execution_authorized':False,
          'bounds':{'max_calendar_days':MAX_DAYS,'max_instances':MAX_INSTANCES}}
    if count>MAX_INSTANCES:raise EventLogWorkspaceError('Calendar analysis supports at most one million instances; narrow the case filter')
    if first is None or last is None:
        return {**base,'available':False,'reason':'No retained intervals are available for this calendar'}
    lo=first.replace(tzinfo=timezone.utc);hi=last.replace(tzinfo=timezone.utc)
    if not 1900<=lo.year<=2100 or not 1900<=hi.year<=2100:
        raise EventLogWorkspaceError('Calendar analysis supports recorded UTC years 1900 through 2100')
    day=lo.astimezone(calendar.tz).date()-timedelta(days=1)
    end=hi.astimezone(calendar.tz).date()+timedelta(days=1)
    if (end-day).days+1>MAX_DAYS:
        raise EventLogWorkspaceError('Calendar analysis spans at most 3660 local dates; narrow the case filter')
    minimum,maximum=lo.timestamp(),hi.timestamp();windows=[]
    while day<=end:
        for a,b in calendar._intervals(day):
            start=max(minimum,calendar.origin+a);finish=min(maximum,calendar.origin+b)
            if finish>start:windows.append((start,finish))
        day+=timedelta(days=1)
    merged=[]
    for start,finish in sorted(windows):
        if merged and start<=merged[-1][1]:merged[-1][1]=max(finish,merged[-1][1])
        else:merged.append([start,finish])
    conn.execute('CREATE OR REPLACE TEMP TABLE working_openings (st TIMESTAMP,en TIMESTAMP,prefix DOUBLE)')
    prefix=0.;rows=[];utc=[];epoch_origin=datetime(1970,1,1,tzinfo=timezone.utc)
    for start,finish in merged:
        a=epoch_origin+timedelta(seconds=start);b=epoch_origin+timedelta(seconds=finish)
        rows.append((a.replace(tzinfo=None),b.replace(tzinfo=None),prefix));utc.append([a.isoformat(),b.isoformat()])
        prefix+=(b-a).total_seconds()
    if rows:conn.executemany('INSERT INTO working_openings VALUES (?,?,?)',rows)
    # At any instant t, its clock is the prefix before the last opening plus
    # the clipped part of that opening. Four ASOF joins avoid n*days expansion.
    def clock(alias,point):
        return f'coalesce({alias}.prefix+epoch(least({alias}.en,{point})-{alias}.st),0)'
    conn.execute(f'''CREATE OR REPLACE TEMP TABLE working_clock AS SELECT s.*,
        {clock('a','s.st')} AS clock_start,{clock('b','s.en')} AS clock_end,
        {clock('c','s.prev_end')} AS clock_previous,
        {clock('d','greatest(s.st,coalesce(s.prev_end,s.st))')} AS clock_busy_start
        FROM seq2 s
        ASOF LEFT JOIN working_openings a ON s.st>=a.st
        ASOF LEFT JOIN working_openings b ON s.en>=b.st
        ASOF LEFT JOIN working_openings c ON s.prev_end>=c.st
        ASOF LEFT JOIN working_openings d ON greatest(s.st,coalesce(s.prev_end,s.st))>=d.st''')
    conn.execute('''CREATE OR REPLACE TEMP TABLE working_seq AS SELECT *,
        CASE WHEN proc IS NULL THEN NULL ELSE least(proc,greatest(0,clock_end-clock_start)) END AS working_proc,
        CASE WHEN prev_end IS NULL THEN NULL ELSE least(wait,greatest(0,clock_start-clock_previous)) END AS working_wait,
        CASE WHEN proc IS NULL THEN 0 ELSE least(busy,greatest(0,clock_end-clock_busy_start)) END AS working_busy
        FROM working_clock''')
    total,outside,excluded=conn.execute('''SELECT coalesce(sum(working_wait),0),
        count(*) FILTER(WHERE proc>working_proc+0.0000005),coalesce(sum(proc-working_proc),0) FROM working_seq''').fetchone()
    acts=conn.execute('''SELECT activity,count(*),count(working_proc),
        quantile_cont(working_proc,0.5),quantile_cont(working_proc,0.9),avg(working_proc),
        count(working_wait),quantile_cont(working_wait,0.5),quantile_cont(working_wait,0.9),avg(working_wait),coalesce(sum(working_wait),0)
        FROM working_seq GROUP BY activity ORDER BY coalesce(sum(working_wait),0) DESC,activity LIMIT 100''').fetchall()
    hotspots=conn.execute('''SELECT prev_activity,activity,count(*),quantile_cont(working_wait,0.5),sum(working_wait)
        FROM working_seq WHERE working_wait IS NOT NULL GROUP BY prev_activity,activity
        ORDER BY sum(working_wait) DESC,prev_activity,activity LIMIT 25''').fetchall()
    flow=conn.execute('''SELECT count(*),quantile_cont(eff,0.5),sum(busy),sum(span) FROM (
        SELECT sum(working_busy) AS busy,max(clock_end)-min(clock_start) AS span,
          sum(working_busy)/(max(clock_end)-min(clock_start)) AS eff FROM working_seq
        GROUP BY case_id HAVING count(proc)>0 AND max(clock_end)>min(clock_start))''').fetchone()
    zero=conn.execute('''SELECT count(*) FROM (SELECT case_id FROM working_seq GROUP BY case_id
        HAVING count(proc)>0 AND max(clock_end)=min(clock_start))''').fetchone()[0]
    def stats(median,p90,mean):return {'median_seconds':median,'p90_seconds':p90,'mean_seconds':mean}
    return {**base,'available':True,'utc_opening_windows_sha256':digest(utc),'utc_opening_windows':len(utc),
        'observed_from_utc':lo.isoformat(),'observed_until_utc':hi.isoformat(),
        'instances_crossing_closed_time':outside,'summed_instance_seconds_outside_calendar':excluded,
        'activities':[{'activity':a,'instances':n,'instances_with_processing_time':known,
            'processing':stats(p50,p90,mean),'waiting_before':{**stats(w50,w90,wmean),'instances':waited,'total_seconds':wsum},
            'share_of_all_waiting':wsum/total if total else None}
            for a,n,known,p50,p90,mean,waited,w50,w90,wmean,wsum in acts],
        'waiting_hotspots':[{'from':s,'to':d,'count':n,'median_waiting_seconds':median,
            'total_waiting_seconds':value,'share_of_all_waiting':value/total if total else None}
            for s,d,n,median,value in hotspots],
        'flow_efficiency':{'cases':flow[0],'median':flow[1],'busy_seconds':flow[2],'elapsed_seconds':flow[3],
                           'cases_with_zero_calendar_span':zero},
        'interpretation':'Intersected with your declared calendar, not verified labor, staffing, policy or source completeness. Elapsed metrics remain separate; closed-time-only efficiency is undefined.'}
