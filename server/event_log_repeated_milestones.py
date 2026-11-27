"""Repeated milestone timing on the exact selected directed relationship pairs.

One row per pair; no Cartesian product of source and target events. Timestamp
ties remain ties. Neither a static link nor elapsed time proves a handoff.
"""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from hashlib import sha256
import json
from statistics import mean

from . import event_log_workspace as workspace

FORMAT="openworkgraph.object-repeated-milestone-review.v1"
STATUSES=("observed_forward","target_only_before_source","target_missing",
          "source_missing","both_missing")


def _stamp(t):
    return t.isoformat()+"Z" if t is not None else None


def _feed(digest,value):
    digest.update(json.dumps(value,ensure_ascii=True,separators=(",",":"),
                             sort_keys=True,allow_nan=False).encode()+b"\n")


def pair_timing(source_id,target_id,source,target,deadline):
    """Sorted timestamp sequences contain one entry per distinct event ID."""
    result={"source_object_id":source_id,"target_object_id":target_id,
            "source_event_occurrences":len(source),"target_event_occurrences":len(target),
            "source_first_at":_stamp(source[0]) if source else None,
            "target_first_at":_stamp(target[0]) if target else None,
            "first_target_on_or_after_source_at":None,"latest_source_at_or_before_target_at":None,
            "first_source_to_subsequent_target_seconds":None,
            "latest_source_to_same_target_seconds":None,
            "elapsed_difference_seconds":None,"target_events_before_first_source":None,
            "target_events_on_or_after_first_source":None,
            "source_events_through_selected_target":None,"source_events_after_selected_target":None,
            "source_events_at_selected_target_timestamp":None,
            "target_events_at_selected_target_timestamp":None,
            "deadline_status":None}
    if not source:
        result["status"]="source_missing" if target else "both_missing"
        return result
    index=bisect_left(target,source[0])
    result["target_events_before_first_source"]=index
    result["target_events_on_or_after_first_source"]=len(target)-index
    if index==len(target):
        result["status"]="target_only_before_source" if target else "target_missing"
        result["deadline_status"]="unresolved" if deadline is not None else None
        return result
    end=target[index]
    through=bisect_right(source,end)
    latest=source[through-1]
    first_seconds=(end-source[0]).total_seconds()
    latest_seconds=(end-latest).total_seconds()
    result.update(status="observed_forward",
        first_target_on_or_after_source_at=_stamp(end),
        latest_source_at_or_before_target_at=_stamp(latest),
        first_source_to_subsequent_target_seconds=first_seconds,
        latest_source_to_same_target_seconds=latest_seconds,
        elapsed_difference_seconds=first_seconds-latest_seconds,
        source_events_through_selected_target=through,
        source_events_after_selected_target=len(source)-through,
        source_events_at_selected_target_timestamp=through-bisect_left(source,end),
        target_events_at_selected_target_timestamp=bisect_right(target,end)-index,
        deadline_status=("within" if first_seconds<=deadline else "observed_late")
            if deadline is not None else None)
    return result


def _summary(values):
    if not values:
        return dict.fromkeys(("minimum","p50","p90","maximum","mean"))
    values=sorted(values)
    def quantile(p):
        i=(len(values)-1)*p
        lo=int(i);hi=min(lo+1,len(values)-1)
        return values[lo]+(values[hi]-values[lo])*(i-lo)
    return {"minimum":values[0],"p50":quantile(.5),"p90":quantile(.9),
            "maximum":values[-1],"mean":mean(values)}


def _read_events(conn,log_id,side,activity,digest):
    # The parent analysis already checks complete distinct selected event
    # counts against its work budget. Each side is read once, not per pair.
    object_column="source_id" if side=="source" else "target_id"
    rows=conn.execute(f"""SELECT DISTINCT eo.object_id,e.ts,e.event_id
        FROM event_objects eo JOIN events e
            ON e.log_id=eo.log_id AND e.event_id=eo.event_id
        WHERE eo.log_id=? AND e.activity=?
          AND EXISTS(SELECT 1 FROM owg_lt_pairs p WHERE p.{object_column}=eo.object_id)
        ORDER BY eo.object_id,e.ts,e.event_id""",[log_id,activity])
    grouped=defaultdict(list)
    while True:
        batch=rows.fetchmany(10000)
        if not batch:break
        for obj,ts,event in batch:
            grouped[obj].append(ts)
            _feed(digest,[side,obj,_stamp(ts),event])
    return grouped


def review(conn,log_id,requested,snapshot):
    basis={"format":FORMAT,"log_id":log_id,
           "relationship_revision":snapshot["revision"],
           "relationship_digest":snapshot["relationships_sha256"],
           "object_index_digest":snapshot["object_index_sha256"],
           **requested.model_dump(include={"source_type","target_type","source_activity",
                                           "target_activity","qualifier","deadline_seconds"})}
    digest=sha256();_feed(digest,basis)
    pairs=conn.execute("SELECT source_id,target_id FROM owg_lt_pairs ORDER BY source_id,target_id").fetchall()
    for pair in pairs:_feed(digest,pair)
    source=_read_events(conn,log_id,"source",requested.source_activity,digest)
    target=_read_events(conn,log_id,"target",requested.target_activity,digest)
    pin=digest.hexdigest()
    if requested.expected_repeat_sha256 is not None and requested.expected_repeat_sha256!=pin:
        raise workspace.EventLogWorkspaceError("Repeated milestone source changed; rerun the review")
    rows=[pair_timing(a,b,source.get(a,()),target.get(b,()),requested.deadline_seconds) for a,b in pairs]
    counts=dict.fromkeys(STATUSES,0);counts.update(Counter(r["status"] for r in rows))
    forward=[r for r in rows if r["status"]=="observed_forward"]
    deadline=None
    if requested.deadline_seconds is not None:
        eligible=sum(bool(r["source_event_occurrences"]) for r in rows)
        within=sum(r["deadline_status"]=="within" for r in rows)
        late=sum(r["deadline_status"]=="observed_late" for r in rows)
        unresolved=eligible-within-late
        deadline={"threshold_seconds":requested.deadline_seconds,
                  "source_observed_pairs":eligible,"within_pairs":within,"observed_late_pairs":late,
                  "unresolved_pairs":unresolved,"source_missing_excluded_pairs":len(rows)-eligible,
                  "within_share_bounds":{"lower":within/eligible if eligible else None,
                    "upper":(within+unresolved)/eligible if eligible else None},
                  "missing_target_not_automatically_late":True}
    matching=[r for r in rows if requested.repeat_status=="all" or r["status"]==requested.repeat_status]
    start=requested.repeat_offset
    page=matching[start:start+requested.sample_limit]
    return {"format":FORMAT,"review_sha256":pin,"basis":basis,"pairs":len(rows),
            "status_counts":counts,
            "first_target_reversed_but_subsequent_target_observed_pairs":
                sum(r["target_events_before_first_source"]>0 for r in forward),
            "latest_source_differs_from_first_pairs":
                sum(r["elapsed_difference_seconds"]>0 for r in forward),
            "first_source_and_target_simultaneous_pairs":
                sum(r["first_source_to_subsequent_target_seconds"]==0 for r in forward),
            "latest_source_and_target_simultaneous_pairs":
                sum(r["latest_source_to_same_target_seconds"]==0 for r in forward),
            "first_source_to_subsequent_target_seconds":_summary([r["first_source_to_subsequent_target_seconds"] for r in forward]),
            "latest_source_to_same_target_seconds":_summary([r["latest_source_to_same_target_seconds"] for r in forward]),
            "elapsed_difference_seconds":_summary([r["elapsed_difference_seconds"] for r in forward]),
            "deadline_review":deadline,
            "page":{"status":requested.repeat_status,"matching_pairs":len(matching),
                    "offset":start,"rows":page,
                    "next_offset":start+len(page) if page and start+len(page)<len(matching) else None},
            "distinct_pair_weighting":True,"event_id_pairing_inferred":False,
            "source_completeness_verified":False,"relationship_validity_period_known":False,
            "execution_authorized":False,
            "interpretation":[
                "First source to first target at or after it, and latest source at or before that same target, are two descriptive elapsed-time anchors for each supplied directed pair.",
                "Multiple source events may precede the selected target; later source events are not included in the latest preceding anchor. Identical timestamps remain ties, not a verified ordering or handoff.",
                "The deadline counts only pairs with an observed source. Missing subsequent targets remain unresolved; earlier targets do not establish a later response. Bounds describe the retained-pair sensitivity under unknown subsequent observations, not a verified SLA or confidence interval.",
                "Every pair has equal weight even when objects are shared. No event matching, historical relationship validity, source completeness, causality or execution approval is inferred."]}
