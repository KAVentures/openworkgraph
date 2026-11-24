"""Three-window historical model selection with training-only inductive discovery.

The training, validation and test cohorts contain distinct cases starting in
their respective time windows. Completion is evaluated at each window's end,
without using later activity to select that window's cases. This is a
retrospective experiment, not a genuinely sealed prospective holdout.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from threading import BoundedSemaphore
from typing import Any

from . import event_log_model_discovery as discovery
from . import event_log_model_quality as quality
from . import event_log_pnml as pnml
from . import event_log_workspace as workspace
from . import event_log_petri_replay as petri

FORMAT = "openworkgraph.temporal-model-quality.v1"
REPORT = "openworkgraph.temporal-model-quality-report.v1"
MAX_EVENTS = 250_000
MAX_CASES = 50_000
MAX_VARIANTS = 300
MAX_TRACE = 120
MAX_LEVELS = 6
MAX_REPLAY_STEPS = 200_000
MAX_NET_NODES = 250
MAX_PAGE = 100
_GUARD = BoundedSemaphore(1)


def _fail(message):
    raise workspace.EventLogWorkspaceError(message)


def _time(value):
    if not isinstance(value, str) or len(value)>40:
        _fail("Use explicit UTC window boundaries")
    try:
        t=datetime.fromisoformat(value.replace("Z","+00:00"))
        if t.tzinfo is None or t.utcoffset()!=timedelta(0):
            raise ValueError()
        return t.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        _fail("Use explicit UTC window boundaries")


def _stamp(t):
    return t.isoformat()+"Z"


def normalize(spec: Any) -> dict:
    allowed={"format","boundaries","end_activities","noise_levels","minimum_cases","case_type"}
    if (not isinstance(spec,dict) or set(spec)-allowed or spec.get("format")!=FORMAT
            or not {"boundaries","end_activities"}<=set(spec)):
        _fail("Provide four UTC boundaries and exact completion labels")
    boundaries=spec["boundaries"]
    if not isinstance(boundaries,list) or len(boundaries)!=4:
        _fail("Provide four UTC boundaries for three non-overlapping windows")
    times=[_time(x) for x in boundaries]
    if times!=sorted(set(times)):
        _fail("Window boundaries must be strictly increasing")
    ends=spec["end_activities"]
    if (not isinstance(ends,list) or not 1<=len(ends)<=20 or
            any(not isinstance(x,str) or not 1<=len(x)<=200 for x in ends)
            or len(set(ends))!=len(ends)):
        _fail("Choose one to twenty distinct exact completion labels")
    levels=spec.get("noise_levels",[0,.1,.2,.4])
    if (not isinstance(levels,list) or not 1<=len(levels)<=MAX_LEVELS or
            any(type(x) not in (int,float) or not 0<=x<1 for x in levels)):
        _fail("Provide one to six noise levels in [0,1)")
    levels=sorted(float(x) for x in levels)
    if len(set(levels))!=len(levels):
        _fail("Noise levels must be distinct")
    minimum=spec.get("minimum_cases",10)
    if type(minimum) is not int or not 1<=minimum<=MAX_CASES:
        _fail("Minimum cases must be an integer between 1 and 50000")
    case_type=spec.get("case_type")
    if case_type is not None and (not isinstance(case_type,str) or not 1<=len(case_type)<=200):
        _fail("Invalid case notion")
    return {"format":FORMAT,"boundaries":[_stamp(x) for x in times],
            "end_activities":sorted(ends),"noise_levels":levels,
            "minimum_cases":minimum,"case_type":case_type}


def _projection(log_id,packet):
    def run(conn):
        n,c,first,last=conn.execute("SELECT count(*),count(DISTINCT case_id),min(ts),max(ts) FROM ce").fetchone()
        if not n or n>MAX_EVENTS or c>MAX_CASES:
            _fail("Empty selection or complete temporal review budget exceeded")
        rows=conn.execute("SELECT case_id,ts,event_id,activity FROM ce ORDER BY case_id,ts,event_id").fetchall()
        histories={};fingerprint=sha256()
        for key,ts,event,activity in rows:
            histories.setdefault(key,[]).append((ts,activity))
            fingerprint.update(json.dumps([key,_stamp(ts),event,activity],ensure_ascii=True,separators=(",",":")).encode()+b"\n")
        return {"histories":histories,"projection_sha256":fingerprint.hexdigest(),"first":first,"last":last,
                "visible_events":n,"visible_cases":c}
    return workspace._query(log_id,packet["case_type"],run)


def partition(histories, boundaries, ends):
    """All cases assigned once by first observed event; only prefix within window is used."""
    windows=[{"name":name,"start":boundaries[i],"end":boundaries[i+1],"cases":[]}
             for i,name in enumerate(("train","validation","test"))]
    outside=0
    for key,history in histories.items():
        first=history[0][0]
        window=next((w for w in windows if w["start"]<=first<w["end"]),None)
        if window is None:
            outside+=1
            continue
        path=[(ts,label) for ts,label in history if ts<window["end"]]
        labels=tuple(a for _,a in path)
        tied=any(a[0]==b[0] and a[1]!=b[1] for a,b in zip(path,path[1:]))
        reason=("ambiguous_timestamp" if tied else
                "not_completed_by_window_end" if labels[-1] not in ends else
                "trace_length_limit" if len(labels)>MAX_TRACE else None)
        window["cases"].append({"case_id":key,"first_at":_stamp(first),"last_at":_stamp(path[-1][0]),
                                "path":labels if reason is None else None,
                                "events":len(labels),"exclusion_reason":reason})
    return windows,outside


def _summarize(window):
    rows=Counter(r["path"] for r in window["cases"] if r["exclusion_reason"] is None)
    if len(rows)>MAX_VARIANTS:
        _fail(f"{window['name']} exceeds the complete distinct-variant budget; narrow the windows")
    excluded=Counter(r["exclusion_reason"] for r in window["cases"] if r["exclusion_reason"])
    return {"name":window["name"],"start":_stamp(window["start"]),"end_exclusive":_stamp(window["end"]),
            "assigned_cases":len(window["cases"]),"eligible_cases":sum(rows.values()),
            "excluded_cases":sum(excluded.values()),"exclusion_reasons":dict(excluded),
            "variants":len(rows)},sorted(rows.items(),key=lambda x:(-x[1],x[0]))


def _build(rows, noise):
    alphabet={a for path,_ in rows for a in path}
    if len(alphabet)>38:
        _fail("Training has more than 38 distinct activities; choose a narrower case notion or window")
    notes=[]
    tree=discovery._discover(rows,noise,0,notes)
    if tree["op"]=="tau":
        _fail("Training produces no visible process model")
    net=pnml.tree_to_net(tree)
    if len(net["places"])+len(net["transitions"])>MAX_NET_NODES:
        _fail("Discovered net exceeds the temporal review complexity budget")
    xml=pnml.to_pnml(net,"Temporal training model")
    return {"noise":noise,"tree":tree,"pnml":xml,"pnml_sha256":sha256(xml.encode()).hexdigest(),
            "simplifications":notes}


def _scores(candidate, rows):
    score=quality._score(candidate["pnml"],rows)
    net=petri.parse_pnml(candidate["pnml"].encode())
    known={label for label in net["transitions"].values() if label is not None}
    events=unknown=unknown_cases=0
    for path,count in rows:
        missing=sum(label not in known for label in path)
        events+=len(path)*count
        unknown+=missing*count
        unknown_cases+=count if missing else 0
    coverage=(events-unknown)/events if events else None
    # Existing greedy replay skips unmodeled labels when computing token
    # counts. Preserve that metric, but explicitly penalize missing labels
    # in the validation selection criterion. This is a declared heuristic,
    # not the standard token fitness or optimal alignment score.
    adjusted=score["fitness"]*coverage if coverage is not None else None
    precision=score["precision"]
    f=None if adjusted is None or precision is None else (
        2*adjusted*precision/(adjusted+precision) if adjusted+precision else 0.0)
    return {**score,"observed_activity_events":events,
            "unknown_activity_events":unknown,"cases_with_unknown_activities":unknown_cases,
            "activity_event_label_coverage":coverage,"coverage_adjusted_fitness":adjusted,
            "coverage_adjusted_f_score":f}


def _winner(candidates):
    comparable=[c for c in candidates if c["validation"]["coverage_adjusted_f_score"] is not None]
    if not comparable:
        _fail("No validation candidate has a comparable coverage-adjusted F-score")
    return max(comparable,key=lambda c:(c["validation"]["coverage_adjusted_f_score"],
        c["validation"]["simplicity"],-c["noise"]))


def _bound(summary,score):
    # Existing quality reports round fitting share. Recover exact case counts
    # by separately replaying weighted variants below, not rounding a fraction.
    n=summary["assigned_cases"];eligible=summary["eligible_cases"]
    fitting=score["fitting_cases_exact"]
    return {"lower":fitting/n if n else None,
            "upper":(fitting+n-eligible)/n if n else None}


def _fit_cases(xml, rows):
    net=petri.parse_pnml(xml.encode())
    labels={}
    for key,value in sorted(net["transitions"].items()):
        if value is not None:
            labels.setdefault(value,[]).append(key)
    results={}
    for path,_ in rows:
        replay=petri._replay(net,tuple(path),labels)
        results[tuple(path)]={"fits":replay["fits"],"fitness":replay["fitness"],
                              "unknown_activities":sorted(set(replay["unknown"])),
                              "missing_tokens":replay["missing"],"remaining_tokens":replay["remaining"]}
    return results


def evaluate(histories,packet):
    boundaries=[_time(x) for x in packet["boundaries"]]
    windows,outside=partition(histories,boundaries,set(packet["end_activities"]))
    train,validation,test=[_summarize(w) for w in windows]
    for summary,_ in (train,validation,test):
        if summary["eligible_cases"]<packet["minimum_cases"]:
            _fail(f"{summary['name']} has fewer than the declared minimum eligible cases")
    # Budget unique paths, not the number of cases represented by each path.
    work=sum(len(p) for p,_ in train[1]+validation[1])*len(packet["noise_levels"])
    work+=2*sum(len(p) for p,_ in test[1])
    if work>MAX_REPLAY_STEPS:
        _fail("Temporal experiment exceeds the replay-step budget")
    candidates=[]
    for noise in packet["noise_levels"]:
        candidate=_build(train[1],noise)
        candidate["training"]=_scores(candidate,train[1])
        candidate["validation"]=_scores(candidate,validation[1])
        candidates.append(candidate)
    selected=_winner(candidates)
    # Only the selected net is ever scored on the test cohort.
    test_score=_scores(selected,test[1])
    exact=_fit_cases(selected["pnml"],test[1])
    test_score["fitting_cases_exact"]=sum(n for p,n in test[1] if exact[p]["fits"])
    test_score["whole_assigned_case_fitting_share_bounds"]=_bound(test[0],test_score)
    test_score["cases_with_unknown_activities"]=sum(n for p,n in test[1] if exact[p]["unknown_activities"])
    unknown_counts=Counter()
    for p,n in test[1]:
        for label in exact[p]["unknown_activities"]:
            unknown_counts[label]+=n
    test_score["unknown_activities_by_case_count"]=dict(sorted(unknown_counts.items()))
    test_cases=[]
    for r in windows[2]["cases"]:
        diagnostics=exact.get(r["path"])
        test_cases.append({**r,"fits_selected_net":diagnostics["fits"] if diagnostics else None,
                           "replay_diagnostics":diagnostics})
    report={"windows":[x[0] for x in (train,validation,test)],"cases_outside_windows":outside,
            "candidates":[{k:v for k,v in c.items() if k not in ("pnml","tree")} for c in candidates],
            "selection":{"noise":selected["noise"],"pnml":selected["pnml"],"pnml_sha256":selected["pnml_sha256"],
                         "basis":"validation_coverage_adjusted_f_score_then_simplicity_then_lower_noise"},
            "test":test_score,"test_scored_candidate_count":1,
            "test_scores_used_for_selection":False,"disjoint_case_membership":True,
            "activity_filter_learned_from_test":False,"historical_not_prospective":True,
            "execution_authorized":False}
    return report,test_cases


def review(log_id,spec,*,page=None):
    packet=normalize(spec)
    if not _GUARD.acquire(blocking=False):
        _fail("Another temporal model review is running")
    try:
        data=_projection(log_id,packet)
        boundaries=[_time(x) for x in packet["boundaries"]]
        if boundaries[-1]>data["last"] and boundaries[-1]-data["last"]>timedelta(microseconds=1):
            _fail("Final boundary must not extend beyond the visible observation horizon")
        identity={"format":FORMAT,"log_id":log_id,"case_notion":data["case_notion"],
                  "inputs":packet,"projection_sha256":data["projection_sha256"]}
        digest=sha256(json.dumps(identity,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
        if page is not None and page["expected_review_sha256"]!=digest:
            _fail("Temporal model review inputs or source changed; run again")
        result,cases=evaluate(data["histories"],packet)
        if page is not None:
            status=page["status"]
            selected=[r for r in cases if status=="all" or
                      (status=="fitting" and r["fits_selected_net"] is True) or
                      (status=="deviating" and r["fits_selected_net"] is False) or
                      (status=="unknown" and r["fits_selected_net"] is None)]
            start=page["offset"];rows=selected[start:start+page["limit"]]
            return {"review_sha256":digest,"selected_pnml_sha256":result["selection"]["pnml_sha256"],
                    "status":status,"total":len(selected),"cases":rows,
                    "next_offset":start+len(rows) if start+len(rows)<len(selected) else None}
        return {**identity,**result,"format":REPORT,"log":data["log"],"review_sha256":digest,
                "visible_events":data["visible_events"],"visible_cases":data["visible_cases"],
                "assumptions":[
                    "Cases are assigned once by their first visible event; each window includes only events strictly before its end.",
                    "Completion means the last visible activity before that boundary matches a declared label. Later reopenings are not used to determine earlier eligibility.",
                    "Ambiguous timestamp ordering, unfinished cases and overlong traces remain excluded and are explicitly counted. Fitting-share bounds include all assigned test cases.",
                    "Training discovers each model on training cases only; validation coverage-adjusted F-score selects one net. Test scores are calculated only for that selected net.",
                    "All raw activity labels are retained. No test-derived vocabulary or activity filter is applied.",
                    "Token replay and precision are the existing bounded greedy replay metrics, not optimal alignment. The declared selection heuristic multiplies token fitness by observed event label coverage before computing its harmonic mean with precision; the original metrics remain visible. Generalization is a heuristic, not a forecast.",
                    "This is reconstructed history. Re-running with different settings after viewing test results can invalidate the holdout interpretation; no truly sealed or independent prospective test is claimed."]}
    finally:
        _GUARD.release()


def evidence(log_id,body):
    if not isinstance(body,dict) or set(body)!={"settings","expected_review_sha256","status","offset","limit"}:
        _fail("Provide reviewed settings, digest, status and page")
    digest=body["expected_review_sha256"]
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
        _fail("Invalid review digest")
    if body["status"] not in ("all","fitting","deviating","unknown"):
        _fail("Invalid test case status")
    if type(body["offset"]) is not int or not 0<=body["offset"]<=MAX_CASES:
        _fail("Invalid evidence offset")
    if type(body["limit"]) is not int or not 1<=body["limit"]<=MAX_PAGE:
        _fail("Invalid evidence page size")
    return review(log_id,body["settings"],page=body)
