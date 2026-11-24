"""Leakage-aware historical landmark validation for next-activity prediction.

One prediction per open case at each explicitly reviewed cutoff. Training sees
only the visible prefix strictly before that cutoff. Outcome labels come from
the first subsequent timestamp, not the final completed-case population.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from threading import BoundedSemaphore
from typing import Any

from . import event_log_next_activity as nxt
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.next-activity-landmarks.v1"
REPORT_FORMAT = "openworkgraph.next-activity-landmark-report.v1"
MAX_EVENTS = 300_000
MAX_CASES = 100_000
MAX_LANDMARKS = 6
MAX_TRACE_EVENTS = 2000
MAX_WORK = 1_200_000
MAX_ROWS = 100
_GUARD = BoundedSemaphore(1)


def _int(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise workspace.EventLogWorkspaceError(f"{name} must be an integer between {low} and {high}")
    return value


def _instant(value):
    if not isinstance(value, str) or len(value) > 40:
        raise workspace.EventLogWorkspaceError("Cutoffs must be explicit UTC timestamps")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError()
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        raise workspace.EventLogWorkspaceError("Cutoffs must be explicit UTC timestamps") from None


def _stamp(value):
    return value.isoformat() + "Z"


def normalize(spec: Any) -> dict:
    fields = {"format", "cutoffs", "horizon_days", "end_activities", "history",
              "min_support", "minimum_training_cases", "case_type"}
    if (not isinstance(spec, dict) or set(spec) - fields or spec.get("format") != FORMAT
            or not {"cutoffs", "horizon_days", "end_activities"} <= set(spec)):
        raise workspace.EventLogWorkspaceError("Provide landmark cutoffs, an observation horizon and exact completion labels")
    dates = spec["cutoffs"]
    if not isinstance(dates, list) or not 1 <= len(dates) <= MAX_LANDMARKS:
        raise workspace.EventLogWorkspaceError("Provide one to six UTC cutoffs")
    cutoffs = sorted(_instant(x) for x in dates)
    if len(set(cutoffs)) != len(cutoffs):
        raise workspace.EventLogWorkspaceError("Cutoffs must be distinct")
    ends = spec["end_activities"]
    if (not isinstance(ends,list) or not 1 <= len(ends) <= 20 or
            any(not isinstance(x,str) or not 1 <= len(x) <= 200 for x in ends) or len(set(ends))!=len(ends)):
        raise workspace.EventLogWorkspaceError("Select one to twenty distinct exact completion labels")
    case_type = spec.get("case_type")
    if case_type is not None and (not isinstance(case_type,str) or not 1<=len(case_type)<=200):
        raise workspace.EventLogWorkspaceError("Invalid case notion")
    days = _int(spec["horizon_days"],1,365,"Observation horizon days")
    try:
        for t in cutoffs:
            t + timedelta(days=days)
    except OverflowError:
        raise workspace.EventLogWorkspaceError("Observation horizon is outside the supported date range") from None
    return {"format":FORMAT,"cutoffs":[_stamp(t) for t in cutoffs],"horizon_days":days,
            "end_activities":sorted(ends),"history":_int(spec.get("history",2),1,4,"History"),
            "min_support":_int(spec.get("min_support",5),1,10000,"Minimum step support"),
            "minimum_training_cases":_int(spec.get("minimum_training_cases",10),1,10000,"Minimum training cases"),
            "case_type":case_type}


def _project(log_id, packet):
    def run(conn):
        count,cases,first,last = conn.execute(
            "SELECT count(*),count(DISTINCT case_id),min(ts),max(ts) FROM ce").fetchone()
        if not count:
            raise workspace.EventLogWorkspaceError("No visible events for landmark evaluation")
        if count>MAX_EVENTS or cases>MAX_CASES or count*len(packet["cutoffs"])>MAX_WORK:
            raise workspace.EventLogWorkspaceError("Landmark evaluation exceeds the complete work budget; reduce cutoffs or select a smaller log")
        if conn.execute("SELECT 1 FROM ce GROUP BY case_id HAVING count(*)>? LIMIT 1",
                        [MAX_TRACE_EVENTS]).fetchone():
            raise workspace.EventLogWorkspaceError("A case exceeds the complete trace budget; no partial evaluation returned")
        rows = conn.execute("SELECT case_id,ts,event_id,activity FROM ce ORDER BY case_id,ts,event_id").fetchall()
        digest = sha256()
        histories = {}
        for key,ts,event,activity in rows:
            digest.update(json.dumps([key,_stamp(ts),event,activity],ensure_ascii=True,
                                     separators=(",",":")).encode()+b"\n")
            histories.setdefault(key,[]).append((ts,activity))
        return {"histories":histories,"projection_sha256":digest.hexdigest(),
                "events":count,"cases":cases,"first":first,"last":last}
    return workspace._query(log_id,packet["case_type"],run)


def _ambiguous(path):
    return any(a[0]==b[0] and a[1]!=b[1] for a,b in zip(path,path[1:]))


def _next_observed(path, start, end):
    if start>=len(path) or path[start][0]>end:
        return None,"no_event_by_observation_end"
    timestamp=path[start][0]
    labels=set()
    for ts,label in path[start:]:
        if ts!=timestamp:
            break
        labels.add(label)
    if len(labels)!=1:
        return None,"ambiguous_next_timestamp"
    return next(iter(labels)),None


def _probabilities(counter,top):
    n=sum(counter.values())
    return [{"activity":a,"case_ends":a is None,"probability":v/n}
            for a,v in nxt._ranked(counter,top)] if n else []


def _score(rows,key,top):
    offered=resolved=hits=0
    for row in rows:
        values=row[key][:top]
        if not values:
            continue
        offered+=1
        if row["outcome_reason"] is None:
            resolved+=1
            hits+=any(v["activity"]==row["actual_next_activity"] for v in values)
    total=len(rows)
    return {"population":total,"predicted":offered,"resolved_predictions":resolved,
            "hits":hits,"unresolved_predictions":offered-resolved,"abstained":total-offered,
            "prediction_coverage":offered/total if total else None,
            "resolved_accuracy":hits/resolved if resolved else None,
            "whole_population_hit_share_bounds":{"lower":hits/total if total else None,
                "upper":(hits+offered-resolved)/total if total else None},
            "predicted_population_accuracy_bounds":{"lower":hits/offered if offered else None,
                "upper":(hits+offered-resolved)/offered if offered else None}}


def _paired(rows,key):
    """Exact top-one hit difference bounds on the common predicted population."""
    n=resolved=delta=lower=upper=0
    for r in rows:
        if not r["model"] or not r[key]:
            continue
        n+=1
        a,b=r["model"][0]["activity"],r[key][0]["activity"]
        if r["outcome_reason"] is None:
            resolved+=1
            d=int(a==r["actual_next_activity"])-int(b==r["actual_next_activity"])
            delta+=d;lower+=d;upper+=d
        elif a!=b:
            # A next activity is an actual label, not the synthetic END symbol.
            lower-=int(b is not None)
            upper+=int(a is not None)
    return {"paired_predictions":n,"paired_resolved":resolved,
            "resolved_hit_share_change":delta/resolved if resolved else None,
            "paired_hit_share_change_bounds":{"lower":lower/n if n else None,"upper":upper/n if n else None}}


def _calibration(rows):
    bins=[{"lower":i/10,"upper":(i+1)/10,"predicted":0,"resolved":0,
           "hits":0,"probability_sum":0.0} for i in range(10)]
    brier=0.0
    for row in rows:
        if not row["model"]:
            continue
        p=row["model"][0]["probability"]
        bucket=bins[min(9,int(p*10))]
        bucket["predicted"]+=1;bucket["probability_sum"]+=p
        if row["outcome_reason"] is None:
            bucket["resolved"]+=1
            hit=int(row["model"][0]["activity"]==row["actual_next_activity"])
            bucket["hits"]+=hit
            brier+=(p-hit)**2
    result=[]
    for b in bins:
        if not b["predicted"]:
            continue
        n=b["predicted"];s=b["resolved"];hits=b["hits"]
        result.append({k:v for k,v in b.items() if k!="probability_sum"} |
                      {"mean_top1_probability":b["probability_sum"]/n,
                       "observed_hit_share_bounds":{"lower":hits/n,"upper":(hits+n-s)/n}})
    scored=sum(b["resolved"] for b in bins)
    return {"bins":result,"top1_correctness_brier_resolved_only":brier/scored if scored else None,
            "resolved_predictions":scored,"not_a_multiclass_brier_score":True}


def evaluate_landmark(histories, cutoff, end, packet):
    ends=set(packet["end_activities"])
    model=nxt._Model(packet["history"])
    training=ambiguous_training=completed_before=0
    candidates=[]
    for key,path in histories.items():
        index=bisect_left([t for t,_ in path],cutoff)
        if not index:
            continue
        prefix=path[:index]
        # Completion is defined only by the last visible pre-cutoff label.
        # Never inspect the later path when deciding who was open at cutoff.
        if prefix[-1][1] in ends:
            completed_before+=1
            if _ambiguous(prefix):
                ambiguous_training+=1
                continue
            model.learn([a for _,a in prefix])
            training+=1
        else:
            candidates.append((key,path,index))
    rows=[]
    enough=training>=packet["minimum_training_cases"]
    overall=model.counts[0].get((),Counter())
    for key,path,index in candidates:
        prefix=path[:index]
        labels=[a for _,a in prefix]
        actual,reason=_next_observed(path,index,end)
        out={"case_id":key,"last_activity":labels[-1],"last_event_at":_stamp(prefix[-1][0]),
             "prefix_events":index,"actual_next_activity":actual,"outcome_reason":reason,
             "model":[],"overall_baseline":[],"last_activity_baseline":[],
             "history_used":None,"support":0,"abstention_reason":None}
        if _ambiguous(prefix):
            out["abstention_reason"]="ambiguous_prefix_timestamp"
        elif not enough:
            out["abstention_reason"]="insufficient_training_cases"
        else:
            counter,level=model.predict(labels,packet["min_support"])
            support=sum(counter.values())
            if support>=packet["min_support"]:
                out["model"]=_probabilities(counter,3)
                out["history_used"]=level;out["support"]=support
            else:
                out["abstention_reason"]="insufficient_step_support"
            if sum(overall.values())>=packet["min_support"]:
                out["overall_baseline"]=_probabilities(overall,1)
            last=model.counts[1].get((labels[-1],),Counter())
            if sum(last.values())>=packet["min_support"]:
                out["last_activity_baseline"]=_probabilities(last,1)
        rows.append(out)
    return {"cutoff":_stamp(cutoff),"observed_until":_stamp(end),
            "completed_before_cutoff":completed_before,"training_cases":training,
            "training_cases_excluded_ambiguous_timestamps":ambiguous_training,
            "open_at_cutoff":len(rows),
            "outcomes":dict(Counter(r["outcome_reason"] or "resolved" for r in rows)),
            "abstentions":dict(Counter(r["abstention_reason"] for r in rows if r["abstention_reason"])),
            "top1":_score(rows,"model",1),"top3":_score(rows,"model",3),
            "overall_baseline":_score(rows,"overall_baseline",1),
            "last_activity_baseline":_score(rows,"last_activity_baseline",1),
            "paired_vs_overall":_paired(rows,"overall_baseline"),
            "paired_vs_last_activity":_paired(rows,"last_activity_baseline"),
            "calibration":_calibration(rows)},rows


def review(log_id,spec,*,evidence=None):
    packet=normalize(spec)
    if not _GUARD.acquire(blocking=False):
        raise workspace.EventLogWorkspaceError("Another landmark evaluation is running")
    try:
        data=_project(log_id,packet)
        cuts=[_instant(x) for x in packet["cutoffs"]]
        if any(c<=data["first"] or c>data["last"] for c in cuts):
            raise workspace.EventLogWorkspaceError("Each cutoff must be after the first visible event and no later than the last")
        identity={"log_id":log_id,"case_notion":data["case_notion"],"inputs":packet,
                  "projection_sha256":data["projection_sha256"]}
        digest=sha256(json.dumps(identity,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
        if evidence is not None:
            if evidence["expected_review_sha256"]!=digest:
                raise workspace.EventLogWorkspaceError("The landmark inputs or projected source changed; run again")
            selected=_instant(evidence["cutoff"])
            if selected not in cuts:
                raise workspace.EventLogWorkspaceError("Select a reviewed cutoff")
            end=min(selected+timedelta(days=packet["horizon_days"]),data["last"])
            _,rows=evaluate_landmark(data["histories"],selected,end,packet)
            status=evidence["status"]
            rows=[r for r in rows if status=="all" or
                  (status=="resolved" and r["outcome_reason"] is None) or
                  (status=="unresolved" and r["outcome_reason"] is not None) or
                  (status=="abstained" and not r["model"])]
            offset=evidence["offset"];page=rows[offset:offset+evidence["limit"]]
            return {"review_sha256":digest,"cutoff":_stamp(selected),"status":status,
                    "total":len(rows),"cases":page,"next_offset":offset+len(page) if offset+len(page)<len(rows) else None}
        reports=[];occurrences=Counter()
        for cutoff in cuts:
            desired=cutoff+timedelta(days=packet["horizon_days"])
            result,rows=evaluate_landmark(data["histories"],cutoff,min(desired,data["last"]),packet)
            result["requested_observation_end"]=_stamp(desired)
            result["observation_window_shortened_by_log_end"]=desired>data["last"]
            reports.append(result)
            occurrences.update(r["case_id"] for r in rows)
        return {"format":REPORT_FORMAT,"log":data["log"],**identity,"review_sha256":digest,
                "visible_events":data["events"],"visible_cases":data["cases"],
                "landmarks":reports,"distinct_open_cases_across_landmarks":len(occurrences),
                "repeated_open_cases_across_landmarks":sum(n>1 for n in occurrences.values()),
                "pooled_independent_accuracy_reported":False,"source_completeness_verified":False,
                "historical_reconstruction_not_prospective_snapshot":True,"execution_authorized":False,
                "assumptions":[
                    "Training and open membership use events strictly before the cutoff; first subsequent events at or after it supply labels.",
                    "Completion means the last visible pre-cutoff activity matches a declared label. A reopened case may appear completed at that cutoff.",
                    "Different activity labels tied at a timestamp are ambiguous. Ambiguous training prefixes are excluded; ambiguous open prefixes abstain.",
                    "The observation horizon is capped at the last visible event. No later event is not evidence that a case ended; such outcomes remain unresolved.",
                    "The existing history model may predict its synthetic case-end label, which cannot match a recorded next activity. This is intentionally not converted to a correct prediction on missing data.",
                    "Only the entire selected case notion is evaluated; retrospective cohort filters may leak future information and are unsupported.",
                    "Each open case contributes once per landmark. Across landmarks cases can repeat; no independent pooled metric or confidence interval is claimed.",
                    "Historical reconstruction cannot establish when records were originally available or whether events were backfilled. It is not a prospective validation or a causal intervention study."]}
    finally:
        _GUARD.release()


def evidence(log_id,body):
    fields={"settings","expected_review_sha256","cutoff","status","offset","limit"}
    if not isinstance(body,dict) or set(body)!=fields:
        raise workspace.EventLogWorkspaceError("Provide reviewed settings, digest, cutoff, status and page")
    digest=body["expected_review_sha256"]
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
        raise workspace.EventLogWorkspaceError("Invalid review digest")
    if body["status"] not in ("all","resolved","unresolved","abstained"):
        raise workspace.EventLogWorkspaceError("Invalid evidence status")
    return review(log_id,body["settings"],evidence={**body,
        "offset":_int(body["offset"],0,MAX_CASES,"Offset"),
        "limit":_int(body["limit"],1,MAX_ROWS,"Page limit")})
