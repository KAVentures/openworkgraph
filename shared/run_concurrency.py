"""Exact half-open interval concurrency on retained reported run timestamps."""
from bisect import bisect_left, bisect_right
from collections import Counter


def summarize(intervals, window_us, capacity):
    """Intervals are (run_id, start_us, end_us), already clipped, positive."""
    if window_us <= 0 or capacity < 1:
        raise ValueError("Invalid review window or capacity")
    seen=set()
    starts=[]
    ends=[]
    changes=Counter({0:0,window_us:0})
    for key,a,b in intervals:
        if key in seen or not 0 <= a < b <= window_us:
            raise ValueError("Invalid clipped interval population")
        seen.add(key)
        starts.append(a)
        ends.append(b)
        changes[a]+=1
        changes[b]-=1
    starts.sort()
    ends.sort()
    load=0
    prev=0
    histogram=Counter()
    segments=[]
    for at in sorted(changes):
        if at>prev:
            histogram[load]+=at-prev
            if segments and segments[-1]["concurrent_runs"]==load:
                segments[-1]["end_us"]=at
            else:
                segments.append({"start_us":prev,"end_us":at,"concurrent_runs":load})
        load+=changes[at]
        if load<0:
            raise ValueError("Invalid interval event sweep")
        prev=at
    if load or sum(histogram.values())!=window_us:
        raise ValueError("Incomplete concurrency sweep")
    rows=[]
    for key,a,b in intervals:
        # Positive overlap only. Excludes this interval, shared endpoints
        # and all nonoverlapping intervals, including nested ones.
        peers=bisect_left(starts,b)-bisect_right(ends,a)-1
        rows.append({"run_id":key,"clipped_start_us":a,"clipped_end_us":b,
                     "duration_us":b-a,"overlapping_run_count":peers})
    overlap=sum(row["overlapping_run_count"] for row in rows)
    occupancy=sum(k*v for k,v in histogram.items())
    if overlap%2 or occupancy!=sum(b-a for _,a,b in intervals):
        raise ValueError("Interval population accounting failed")
    return {"summary":{"measured_intervals":len(intervals),"window_us":window_us,
        "peak_concurrent_runs":max(histogram),"occupied_us":window_us-histogram[0],
        "above_capacity_us":sum(v for k,v in histogram.items() if k>capacity),
        "at_capacity_us":histogram[capacity],"reported_run_elapsed_sum_us":occupancy,
        "excess_run_us":sum((k-capacity)*v for k,v in histogram.items() if k>capacity),
        "overlapping_run_pairs":overlap//2,
        "runs_with_overlap":sum(r["overlapping_run_count"]>0 for r in rows)},
        "histogram":[{"concurrent_runs":k,"duration_us":v} for k,v in sorted(histogram.items()) if v],
        "segments":segments,
        "runs":sorted(rows,key=lambda r:(-r["overlapping_run_count"],r["run_id"]))}
