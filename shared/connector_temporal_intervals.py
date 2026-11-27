"""Conservative temporal relations on reported evidence, not actual handoffs.

A point denotes a reported timestamp. A day interval denotes an operator
alignment of a business-date label, NOT proof of when a physical action
occurred. Closed intervals deliberately do not infer strict ordering at
a shared boundary. Missing timezone alignment means unknown, not UTC.
"""
from bisect import bisect_left
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EPOCH=datetime(1970,1,1,tzinfo=timezone.utc)
ZONE=re.compile(r"(?:UTC|[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)+)\Z")
MICROS=1_000_000


def micros(dt):
    d=dt.astimezone(timezone.utc)-EPOCH
    return (d.days*86400+d.seconds)*MICROS+d.microseconds


def seconds(microseconds):
    return microseconds/MICROS


@lru_cache(maxsize=64)
def zone(name):
    if (not isinstance(name,str) or len(name)>80 or not ZONE.fullmatch(name)
        or name.startswith(("posix/","right/"))):
        raise ValueError("use an explicit IANA timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError,ValueError):
        raise ValueError("IANA timezone unavailable") from None


def midnight(day,tz):
    naive=datetime.combine(day,time.min)
    # Some jurisdictions move clocks at midnight or skip entire dates.
    # An ambiguous or nonexistent endpoint is not silently normalized.
    candidates=set()
    for fold in (0,1):
        instant=naive.replace(tzinfo=tz,fold=fold).astimezone(timezone.utc)
        if instant.astimezone(tz).replace(tzinfo=None)==naive:
            candidates.add(instant)
    if len(candidates)!=1:
        raise ValueError("ambiguous or nonexistent business-date boundary")
    return candidates.pop()


def reported_interval(value,*,day=False,timezone_name=None):
    """Integer microsecond [lower, upper] or None for unknown alignment."""
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        if dt.utcoffset() is None:
            raise ValueError("timezone-qualified timestamp required")
        if not day:
            point=micros(dt)
            return point,point
        if dt.utcoffset()!=timedelta(0) or dt.time()!=time.min:
            raise ValueError("business-date label must use canonical UTC midnight")
        if timezone_name is None:
            return None
        tz=zone(timezone_name)
        lower=micros(midnight(dt.date(),tz))
        upper=micros(midnight(dt.date()+timedelta(days=1),tz))
        if upper<=lower:
            raise ValueError("invalid business-date interval")
        return lower,upper
    except (AttributeError,TypeError,OverflowError):
        raise ValueError("invalid reported timestamp") from None


def compare(left,right):
    """Count all inter-source pairs in O((n+m) log(n+m)) time.

    Strict ordering is conditional on the supplied reported-time intervals.
    Equal point timestamps, overlapping uncertain intervals, and unknown
    date alignment are mutually exclusive categories.
    """
    a=[x for x in left if x is not None]
    b=[x for x in right if x is not None]
    ends_a=sorted(hi for _,hi in a)
    ends_b=sorted(hi for _,hi in b)
    a_before=sum(bisect_left(ends_a,lo) for lo,_ in b)
    b_before=sum(bisect_left(ends_b,lo) for lo,_ in a)
    points_a=Counter(lo for lo,hi in a if lo==hi)
    points_b=Counter(lo for lo,hi in b if lo==hi)
    ties=sum(n*points_b[t] for t,n in points_a.items())
    pairs=len(left)*len(right)
    known=len(a)*len(b)
    overlap=known-a_before-b_before-ties
    separated=None
    gap=None
    if pairs and known==pairs and a_before==pairs:
        separated="left_before_right"
        gap=boundary_gap(a,b)
    elif pairs and known==pairs and b_before==pairs:
        separated="right_before_left"
        gap=boundary_gap(b,a)
    return {"separation":separated,"reported_boundary_gap_bounds":gap,
            "event_pairs":pairs,"left_strictly_before_right":a_before,
            "right_strictly_before_left":b_before,"equal_reported_points":ties,
            "overlapping_intervals":overlap,"unaligned_date_pairs":pairs-known}


def boundary_gap(earlier,later):
    """Gap between latest earlier and earliest later reported representatives.

    Called only for wholly separated source intervals. This is not a
    process wait time, handoff duration, or proof of inactivity.
    """
    lo=min(x[0] for x in later)-max(x[1] for x in earlier)
    hi=min(x[1] for x in later)-max(x[0] for x in earlier)
    return {"minimum_seconds":seconds(lo),"maximum_seconds":seconds(hi)}


def span(intervals):
    """Extrema of the span across distinct event representatives.

    For intervals with independent allowed representatives, minimum span is
    max(0, max lower - min upper). Maximum uses distinct event indices.
    A single event always has zero span. Unknown alignment suppresses both.
    """
    if not intervals or any(x is None for x in intervals):
        return None
    if len(intervals)==1:
        return {"minimum_seconds":0.0,"maximum_seconds":0.0}
    lower=max(0,max(lo for lo,_ in intervals)-min(hi for _,hi in intervals))
    # Top two upper endpoints suffice even when the same event supplies
    # the earliest lower endpoint and latest upper endpoint.
    largest=sorted(((hi,i) for i,(_,hi) in enumerate(intervals)),reverse=True)[:2]
    upper=max((largest[0][0] if largest[0][1]!=i else largest[1][0])-lo
              for i,(lo,_) in enumerate(intervals))
    return {"minimum_seconds":seconds(lower),"maximum_seconds":seconds(max(0,upper))}
