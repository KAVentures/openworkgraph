"""Aggregate response episodes in one explicitly selected communications export.

No text, identity, raw thread key, channel label or per-thread report is emitted.
An outbound message is only an observed reply proxy, not verified resolution.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

FORMAT="openworkgraph.communications-response-episodes.v1"
MIN_THREADS=5
MAX_DEADLINES=8
MAX_MINUTES=43200
MAX_GROUPS=100
MAX_WEEKS=26
AGE_BANDS=(60,240,1440,4320,10080,43200)
STATES=("observed_within_deadline","observed_after_deadline","no_reply_deadline_elapsed","no_reply_within_deadline")


def settings(cutoff,deadlines):
    if not isinstance(cutoff,str) or len(cutoff)>64:
        raise ValueError("declare a timezone-qualified observation cutoff")
    try:
        stamp=datetime.fromisoformat(cutoff.replace("Z","+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:raise ValueError
        stamp=stamp.astimezone(timezone.utc)
    except (ValueError,OverflowError):
        raise ValueError("declare a valid timezone-qualified observation cutoff") from None
    if stamp>datetime.now(timezone.utc):
        raise ValueError("observation cutoff cannot be in the future")
    if (type(deadlines) is not list or not 1<=len(deadlines)<=MAX_DEADLINES or
        any(type(x) is not int or not 1<=x<=MAX_MINUTES for x in deadlines) or len(set(deadlines))!=len(deadlines)):
        raise ValueError("choose one to eight distinct whole-minute deadlines from 1 to 43200")
    return stamp,sorted(deadlines)


def _seconds(a,b):
    # Integer microseconds avoid rounding at an exact deadline boundary.
    dt=b-a
    return (dt.days*86400+dt.seconds)*1000000+dt.microseconds


def _episodes(items,cutoff):
    """Eligible thread with known directions and nonambiguous time ordering."""
    groups=defaultdict(list)
    for stamp,direction,label in items:
        groups[stamp].append((direction,label))
    episodes=[]
    current=None
    for stamp,group in sorted(groups.items()):
        direction=group[0][0]
        if direction=="inbound":
            if current is None:
                labels={label for _,label in group}
                current={"started":stamp,"first_intent":next(iter(labels)) if len(labels)==1 else None,
                         "inbound_messages":0,"kind":"first_observed" if not episodes else "follow_up"}
            current["inbound_messages"]+=len(group)
        elif current is not None:
            current.update(reply=stamp,elapsed_us=_seconds(current["started"],stamp))
            episodes.append(current);current=None
    if current is not None:
        current.update(reply=None,elapsed_us=_seconds(current["started"],cutoff))
        episodes.append(current)
    return episodes


def _state(row,minutes):
    elapsed=row["elapsed_us"]
    within=elapsed<=minutes*60*1000000
    if row["reply"] is not None:
        return STATES[0] if within else STATES[1]
    # At exactly the deadline an unobserved reply could still meet it.
    return STATES[3] if within else STATES[2]


def _p90(values):
    return values[(9*len(values)+9)//10-1] if values else None


def summarize(rows,deadlines):
    n=len(rows)
    observed=sorted(r["elapsed_us"]/1000000 for _,r in rows if r["reply"] is not None)
    waiting=sorted(r["elapsed_us"]/1000000 for _,r in rows if r["reply"] is None)
    targets=[]
    for minutes in deadlines:
        counts=Counter(_state(row,minutes) for _,row in rows)
        met,late,overdue,pending=(counts[s] for s in STATES)
        targets.append({"deadline_minutes":minutes,"counts":{k:counts[k] for k in STATES},
                        "observed_within_deadline_share":met/n if n else None,
                        "upper_share_if_pending_reply_on_time":(met+pending)/n if n else None,
                        "observed_late_or_elapsed_without_reply":late+overdue,
                        "unresolved_within_deadline":pending})
    return {"distinct_threads":len({key for key,_ in rows}),"episodes":n,
            "first_observed_episodes":sum(r["kind"]=="first_observed" for _,r in rows),
            "follow_up_episodes":sum(r["kind"]=="follow_up" for _,r in rows),
            "inbound_messages":sum(r["inbound_messages"] for _,r in rows),
            "additional_inbound_before_observed_reply_or_cutoff":sum(r["inbound_messages"]-1 for _,r in rows),
            "episodes_with_repeat_inbound":sum(r["inbound_messages"]>1 for _,r in rows),
            "observed_reply_episodes":len(observed),"without_observed_reply":len(waiting),
            "observed_reply_seconds":{"median":median(observed) if observed else None,"p90_nearest_rank":_p90(observed),"count":len(observed)},
            "open_age_seconds":{"median":median(waiting) if waiting else None,"p90_nearest_rank":_p90(waiting),"count":len(waiting)},
            "deadlines":targets}


def _grouped(groups,deadlines):
    # Support is distinct threads, not volume of repeat episodes. Suppressed
    # group names and timing distributions are never returned.
    eligible=[(key,rows) for key,rows in groups.items() if len({t for t,_ in rows})>=MIN_THREADS]
    eligible.sort(key=lambda item:(-len(item[1]),str(item[0])))
    visible=eligible[:MAX_GROUPS]
    visible_keys={key for key,_ in visible}
    omitted=[rows for key,rows in groups.items() if key not in visible_keys]
    return {"rows":[{"group_kind":key[0],"label":key[1],**summarize(rows,deadlines)} for key,rows in visible],
            "suppressed_or_limited_groups":len(omitted),
            "episodes_in_suppressed_or_limited_groups":sum(len(r) for r in omitted),
            "minimum_distinct_threads":MIN_THREADS,"maximum_groups":MAX_GROUPS}


def _window(rows,start,end,*,include_end):
    """Sweep episode endpoints in a window, using exact integer exposure.

    Starts/replies at the same timestamp are applied together. Opening
    backlog is immediately before the start; closing backlog is before end
    unless the final inclusive observation cutoff is selected.
    """
    changes=defaultdict(int)
    support=set()
    opening=arrivals=replies=exposure=0
    def inside(t):
        return start<=t and (t<end or include_end and t==end)
    for key,row in rows:
        a,b=row["started"],row["reply"]
        if a>end or (a==end and not include_end) or (b is not None and b<start):
            continue
        support.add(key)
        if a<start and (b is None or b>=start):opening+=1
        if inside(a):
            arrivals+=1;changes[a]+=1
        if b is not None and inside(b):
            replies+=1;changes[b]-=1
        left,right=max(a,start),min(b if b is not None else end,end)
        if right>left:exposure+=_seconds(left,right)
    backlog=peak=opening
    for stamp,delta in sorted(changes.items()):
        backlog+=delta
        if backlog<0:raise ValueError("inconsistent observed response episode accounting")
        peak=max(peak,backlog)
    if backlog!=opening+arrivals-replies:raise ValueError("inconsistent response window balance")
    span=_seconds(start,end)
    return {"opening_observed_backlog":opening,"new_episodes":arrivals,"observed_replies":replies,
            "closing_observed_backlog":backlog,"net_backlog_change":arrivals-replies,
            "peak_observed_backlog":peak,"episode_wait_seconds":exposure/1000000,
            "time_weighted_observed_backlog":exposure/span if span else None,
            "distinct_threads_touched":len(support)}


def workload(rows,cutoff,deadlines):
    if not rows:
        return {"available":False,"reason":"no_eligible_episodes","weekly_windows":[],"aging_bands":[]}
    first=min(row["started"] for _,row in rows)
    overall=_window(rows,first,cutoff,include_end=True)
    # Display at most the last 26 UTC calendar weeks, with a partial first
    # or final week where necessary. Earlier starts are still included in
    # opening backlog and exposure. No per-event timestamp is exported.
    monday=cutoff.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=cutoff.weekday())
    first_monday=first.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=first.weekday())
    earliest=max(first_monday,monday-timedelta(weeks=min(MAX_WEEKS-1,(monday-first_monday).days//7)))
    start=max(earliest,first)
    weekly=[];suppressed=0
    while start<=cutoff:
        boundary=start.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=7-start.weekday())
        end=min(boundary,cutoff)
        final=end==cutoff and end<boundary
        # If cutoff lies exactly at the next Monday, leave boundary events
        # to a final zero-span bucket so no event is counted twice.
        metrics=_window(rows,start,end,include_end=final)
        y,w,_=start.isocalendar()
        if metrics["distinct_threads_touched"]>=MIN_THREADS:
            weekly.append({"week":f"{y}-W{w:02d}","partial_week":start!=start.replace(hour=0,minute=0,second=0,microsecond=0) or start.weekday()!=0 or final,
                           **metrics})
        else:suppressed+=1
        if final:break
        start=end
    open_rows=[row for _,row in rows if row["reply"] is None]
    ages=[row["elapsed_us"] for row in open_rows]
    bands=[]
    lower=0
    for upper in (*AGE_BANDS,None):
        count=sum(age>=lower*60*1000000 and (upper is None or age<upper*60*1000000) for age in ages)
        bands.append({"minimum_age_minutes_inclusive":lower,"maximum_age_minutes_exclusive":upper,"open_episodes":count})
        lower=upper
    aging=[]
    for minutes in deadlines:
        target=minutes*60*1000000
        remaining=[(target-age)/1000000 for age in ages if age<=target]
        overdue=[(age-target)/1000000 for age in ages if age>target]
        aging.append({"deadline_minutes":minutes,"open_episodes":len(ages),
                      "past_deadline":len(overdue),"within_or_at_deadline":len(remaining),
                      "due_within_next_hour":sum(x<=3600 for x in remaining),
                      "due_within_next_day":sum(x<=86400 for x in remaining),
                      "median_seconds_past_deadline":median(overdue) if overdue else None})
    return {"available":True,"overall":overall,"observation_span_seconds":_seconds(first,cutoff)/1000000,
            "weekly_windows":weekly,"weekly_windows_withheld_below_support":suppressed,
            "minimum_distinct_threads_per_week":MIN_THREADS,"maximum_weeks":MAX_WEEKS,
            "older_weeks_not_displayed":earliest>first_monday,"aging_bands":bands,"open_deadline_exposure":aging,
            "interpretation":"Backlog is the number of eligible observed response episodes awaiting the next recorded outbound. "
                "Episode-wait seconds measure elapsed exposure, not staff work time. Weekly endpoints are UTC, "
                "simultaneous events are netted, and small weekly groups are withheld. Due counts are relative to the declared cutoff, not current live workload. "
                "No staffing capacity, resolution, service compliance or missing source messages are inferred."}


def analyze(threads,*,cutoff,deadlines,unthreaded_messages):
    rows=[]
    counts=Counter()
    exclusions=Counter()
    for key,items in threads.items():
        # Undated records cannot be assigned to either side of the cutoff;
        # withhold the entire thread, even if its dated messages look complete.
        if any(stamp is None for stamp,_,_ in items):
            exclusions["undated_message"]+=1
            continue
        before=[(stamp,direction,label) for stamp,direction,label in items if stamp<=cutoff]
        counts["messages_after_cutoff"]+=len(items)-len(before)
        if not before:
            exclusions["only_after_cutoff"]+=1;continue
        if any(direction not in {"inbound","outbound"} for _,direction,_ in before):
            exclusions["unknown_direction"]+=1;continue
        directions=defaultdict(set)
        for stamp,direction,_ in before:directions[stamp].add(direction)
        if any(len(d)>1 for d in directions.values()):
            exclusions["mixed_direction_timestamp_tie"]+=1;continue
        if not any(direction=="inbound" for _,direction,_ in before):
            exclusions["no_observed_inbound"]+=1;continue
        counts["eligible_threads"]+=1
        counts["eligible_messages"]+=len(before)
        episodes=_episodes(before,cutoff)
        counts["threads_with_follow_up"]+=len(episodes)>1
        counts["threads_with_open_episode"]+=episodes[-1]["reply"] is None
        rows.extend((key,episode) for episode in episodes)
    by_intent=defaultdict(list);by_week=defaultdict(list);by_kind=defaultdict(list)
    for key,row in rows:
        label=row["first_intent"]
        by_intent[("first_inbound_label",label) if label is not None else ("mixed_first_timestamp",None)].append((key,row))
        y,w,_=row["started"].isocalendar()
        by_week[("episode_start_utc_week",f"{y}-W{w:02d}")].append((key,row))
        by_kind[("episode_kind",row["kind"])].append((key,row))
    return {"format":FORMAT,"observation_cutoff":cutoff.isoformat().replace("+00:00","Z"),
            "deadline_minutes":deadlines,"coverage":{"threaded_population":len(threads),
                "eligible_threads":counts["eligible_threads"],"eligible_messages":counts["eligible_messages"],
                "unthreaded_messages":unthreaded_messages,"excluded_threads":sum(exclusions.values()),
                "exclusive_exclusion_reasons":dict(sorted(exclusions.items())),
                "dated_messages_after_cutoff_in_fully_dated_threads":counts["messages_after_cutoff"],
                "threads_with_follow_up":counts["threads_with_follow_up"],
                "threads_with_open_episode":counts["threads_with_open_episode"]},
            "overall":summarize(rows,deadlines),"workload":workload(rows,cutoff,deadlines),
            "by_first_inbound_intent":_grouped(by_intent,deadlines),
            "by_episode_start_week":_grouped(by_week,deadlines),"by_episode_kind":_grouped(by_kind,deadlines),
            "thread_identifiers_returned":False,"message_text_returned":False,
            "export_completeness_verified":False,"actual_response_or_resolution_verified":False,
            "production_sla_verified":False,"execution_authorized":False,
            "interpretation":"An episode starts at the first observed inbound message after a prior reply or at the first observed inbound of a thread. Additional inbound messages before the next observed outbound are repeat contacts. The next outbound is a reply proxy, not resolution. "
                "Deadline shares are conditional on this selected export and cutoff; omitted replies or earlier contacts may change them. They are not confidence intervals or verified SLA compliance. "
                "Undated or ambiguous-direction/timestamp threads are withheld. First observed does not mean first ever. Group labels are classifier estimates; subgroup support counts distinct threads."}
