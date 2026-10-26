"""Typed descriptive queries over a bounded, already authorized coded case dataset."""
from collections import defaultdict
from math import floor
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator
from .analysis_snapshot import Strict, content_digest
from .analysis_case_dataset import MAX_ELAPSED_US

Code = Annotated[str, Field(pattern=r'^A[1-9][0-9]{0,2}$')]
Safe = Annotated[StrictInt, Field(ge=-(2**53-1), le=2**53-1)] | Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-(2**53-1), le=2**53-1)]
Seconds = Annotated[StrictInt, Field(ge=0,le=MAX_ELAPSED_US//1000000)] | Annotated[float, Field(strict=True,allow_inf_nan=False,ge=0,le=MAX_ELAPSED_US//1000000)]


class Filter(Strict):
    activity: Code | None = None
    variant: Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')] | None = None
    min_duration_seconds: Seconds | None = None
    max_duration_seconds: Seconds | None = None
    min_events: Annotated[StrictInt,Field(ge=1,le=5000)] | None = None
    repeated_activity: StrictBool | None = None

    @model_validator(mode='after')
    def interval(self):
        if self.min_duration_seconds is not None and self.max_duration_seconds is not None and self.min_duration_seconds>self.max_duration_seconds:
            raise ValueError('minimum duration exceeds maximum')
        return self


class Measure(Strict):
    fn: Literal['count','share','sum','avg','median','p90','min','max']
    of: Literal['duration_seconds','events','activity_count','time_between'] | None = None
    activity: Code | None = None
    from_activity: Code | None = None
    to_activity: Code | None = None

    @model_validator(mode='after')
    def selector(self):
        if (self.fn in {'count','share'}) != (self.of is None):
            raise ValueError('count/share take no value selector; numeric aggregates require one')
        if (self.of=='activity_count') != (self.activity is not None):
            raise ValueError('activity_count needs exactly one activity selector')
        if self.of=='time_between':
            if self.from_activity is None or self.to_activity is None:raise ValueError('time_between needs both activity selectors')
        elif self.from_activity is not None or self.to_activity is not None:
            raise ValueError('from/to apply only to time_between')
        return self


class Check(Strict):
    measure_index: Annotated[StrictInt, Field(ge=0,le=3)] = 0
    comparison: Literal['gte','gt','lte','lt','eq']
    threshold: Safe


class Query(Strict):
    format: Literal['openworkgraph.shared-case-analysis.v1'] = 'openworkgraph.shared-case-analysis.v1'
    where: Filter = Field(default_factory=Filter)
    group_by: Literal['all','first_activity','last_activity','variant','repeated_activity'] = 'all'
    measures: list[Measure] = Field(min_length=1,max_length=4)
    order: Literal['first_measure_desc','group_asc'] = 'first_measure_desc'
    limit: Annotated[StrictInt, Field(ge=1,le=100)] = 100
    check: Check | None = None

    @model_validator(mode='after')
    def check_slot(self):
        if self.check and self.check.measure_index>=len(self.measures):raise ValueError('check points to a missing measure')
        return self


def _measure(events, path, m):
    if m.of=='duration_seconds':return events[-1]['elapsed_microseconds']/1000000
    if m.of=='events':return len(events)
    if m.of=='activity_count':return path.count(m.activity)
    if m.of=='time_between':
        start=next((i for i,a in enumerate(path) if a==m.from_activity),None)
        if start is None:return None
        finish=next((i for i in range(start+1,len(path)) if path[i]==m.to_activity),None)
        if finish is None:return None
        return (events[finish]['elapsed_microseconds']-events[start]['elapsed_microseconds'])/1000000
    return 1


def _aggregate(values, fn, total):
    if fn=='count':return len(values)
    if fn=='share':return len(values)/total if total else None
    if not values:return None
    if fn=='sum':return sum(values)
    if fn=='avg':return sum(values)/len(values)
    if fn=='min':return min(values)
    if fn=='max':return max(values)
    values=sorted(values);k=(len(values)-1)*(0.5 if fn=='median' else 0.9);lo=floor(k);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(k-lo)


def execute(packet, query):
    """No code/SQL execution, source lookup or reads outside this single packet."""
    q=query if isinstance(query,Query) else Query.model_validate(query)
    groups=defaultdict(list);all_values=[];where=q.where;matched_events=0
    for case in packet['cases']:
        events=case['events'];path=[e['activity'] for e in events];duration=events[-1]['elapsed_microseconds']/1000000
        repeated=len(set(path))<len(path);variant=content_digest(path)
        if ((where.activity is not None and where.activity not in path) or (where.variant is not None and where.variant!=variant)
            or (where.min_duration_seconds is not None and duration<where.min_duration_seconds)
            or (where.max_duration_seconds is not None and duration>where.max_duration_seconds)
            or (where.min_events is not None and len(events)<where.min_events)
            or (where.repeated_activity is not None and repeated!=where.repeated_activity)):
            continue
        key={'all':'All selected cases','first_activity':path[0],'last_activity':path[-1],
             'variant':variant,'repeated_activity':'repeated activity' if repeated else 'no repeated activity'}[q.group_by]
        values=[_measure(events,path,m) for m in q.measures];groups[key].append(values);all_values.append(values);matched_events+=len(events)
    if q.group_by=='all' and not groups:groups['All selected cases']=[]
    total=len(all_values);rows=[]
    for key,cases in groups.items():
        values=[];coverage=[]
        for i,m in enumerate(q.measures):
            present=[c[i] for c in cases if c[i] is not None]
            values.append(_aggregate(present,m.fn,total));coverage.append({'with_value':len(present),'without_value':len(cases)-len(present)})
        state=None
        if q.check:
            slot=q.check.measure_index;value=values[slot];missing=coverage[slot]['without_value']
            if value is None:state='not_evaluable'
            elif missing:state='incomplete'
            else:
                bound=q.check.threshold
                passed={'gte':value>=bound,'gt':value>bound,'lte':value<=bound,'lt':value<bound,'eq':value==bound}[q.check.comparison]
                state='passes' if passed else 'fails'
        rows.append({'group':key,'cases':len(cases),'values':values,'coverage':coverage,'check_state':state})
    if q.order=='group_asc':rows.sort(key=lambda r:r['group'])
    else:rows.sort(key=lambda r:(r['values'][0] is None,-r['values'][0] if r['values'][0] is not None else 0,r['group']))
    returned=rows[:q.limit]
    columns=[{'heading':f'Measure {i+1}','aggregation':m.fn,'value_basis':m.of,
        'unit':'cases' if m.fn=='count' else 'share' if m.fn=='share' else 'seconds' if m.of in {'duration_seconds','time_between'} else 'events' if m.of=='events' else 'occurrences'} for i,m in enumerate(q.measures)]
    return {'spec':q.model_dump(mode='json'),'spec_sha256':content_digest(q.model_dump(mode='json')),
        'columns':columns,'rows':returned,'matching_cases':total,'matching_events':matched_events,'dataset_cases':len(packet['cases']),
        'groups':len(rows),'rows_truncated':len(rows)>q.limit,
        'coverage':[{'with_value':sum(c[i] is not None for c in all_values),'without_value':sum(c[i] is None for c in all_values)} for i in range(len(q.measures))],
        'check_coverage':{'returned_rows':len(returned),'rows_not_returned':len(rows)-len(returned),
            'incomplete_rows':sum(r['check_state']=='incomplete' for r in returned),'not_evaluable_rows':sum(r['check_state']=='not_evaluable' for r in returned)} if q.check else None,
        'check_is_live_monitoring':False,'check_is_business_policy_approval':False,'execution_authorized':False,
        'basis':'publisher_supplied_immutable_coded_case_dataset',
        'time_between_basis':'first from occurrence to first later to occurrence in event order; ties do not establish causality',
        'elapsed_time_is_employee_effort':False,'source_independently_verified':False}
