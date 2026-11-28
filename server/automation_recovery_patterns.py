"""Exact issue sequences and later reported completion episodes in retained runs.

These are ordered report observations, not inferred retries, incident causes,
verified recovery, or business outcomes. No runtime is contacted.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import automation_monitoring as m
from . import automation_run_evidence as e

FORMAT="openworkgraph.automation-recovery-patterns.v1"
MAX_RUNS=10000
MAX_STEPS=250000
MAX_GROUPS=20000
MAX_PAGE=50
ISSUES={"failed","handed_to_human"}
STATES=("later_completion","running_without_later_completion","terminal_without_later_completion")
VIEWS=("patterns","steps","transitions","runs")


class Request(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    reported_spec_sha256: str|None=Field(pattern=e.HASH_PATTERN)
    start_date: str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    view: Literal["patterns","steps","transitions","runs"]="patterns"
    run_filter: Literal["all","has_issue","no_issue","later_completion","unresolved_episode","succeeded_with_issue"]="all"
    pattern_sha256: str|None=Field(default=None,pattern=e.HASH_PATTERN)
    step_id: str|None=Field(default=None,pattern=e.ID_PATTERN)
    transition_sha256: str|None=Field(default=None,pattern=e.HASH_PATTERN)
    expected_population_sha256: str|None=Field(default=None,pattern=e.HASH_PATTERN)
    offset: int=Field(default=0,ge=0,le=MAX_GROUPS)
    limit: int=Field(default=25,ge=1,le=MAX_PAGE)


def analyze_run(run):
    """One open episode per stable step ID until a later completion of that ID.

    Intervening other steps and skipped occurrences do not resolve an episode.
    A later issue after completion starts a separate episode. Handoff and
    failure can coexist in one episode; neither implies an actual retry.
    """
    steps=run["steps"]
    if len(steps)>m.MAX_STEPS_PER_RUN or len({s["seq"] for s in steps})!=len(steps):
        raise m.MonitoringError("retained step order is ambiguous or oversized")
    if any(s["status"] not in ISSUES|{"completed","skipped"} for s in steps):
        raise m.MonitoringError("unsupported retained step status")
    if run["status"] not in m.RUN_STATUSES:
        raise m.MonitoringError("unsupported retained run status")
    opened={}
    episodes=[]
    sequence=[]
    for s in sorted(steps,key=lambda s:s["seq"]):
        ident=s["step_id"]
        if s["status"] in ISSUES:
            sequence.append([ident,s["status"]])
            if ident not in opened:
                opened[ident]={"step_id":ident,"first_issue_seq":s["seq"],"first_issue_at":s["at"],
                    "last_issue_seq":s["seq"],"last_issue_at":s["at"],"issue_occurrences":0,
                    "failure_occurrences":0,"handoff_occurrences":0}
            row=opened[ident]
            row["issue_occurrences"]+=1
            row["failure_occurrences"]+=s["status"]=="failed"
            row["handoff_occurrences"]+=s["status"]=="handed_to_human"
            row["last_issue_seq"]=s["seq"];row["last_issue_at"]=s["at"]
        elif s["status"]=="completed" and ident in opened:
            row=opened.pop(ident)
            elapsed=(m._time(s["at"],"step time")-m._time(row["first_issue_at"],"step time")).total_seconds()
            row.update(state="later_completion",completion_seq=s["seq"],completion_at=s["at"],
                       first_issue_to_completion_seconds=elapsed if elapsed>=0 else None,
                       timestamp_order_inconsistent=elapsed<0)
            episodes.append(row)
    for row in opened.values():
        row.update(state="running_without_later_completion" if run["status"]=="running" else "terminal_without_later_completion",
                   completion_seq=None,completion_at=None,first_issue_to_completion_seconds=None,
                   timestamp_order_inconsistent=False)
        episodes.append(row)
    episodes.sort(key=lambda r:r["first_issue_seq"])
    pattern=e._digest(sequence)
    transitions=Counter((tuple(a),tuple(b)) for a,b in zip(sequence,sequence[1:]))
    return {"pattern_sha256":pattern,"issue_sequence":sequence,"episodes":episodes,
            "transition_counts":transitions}


def _counts(values):
    return dict(sorted(Counter(values).items()))


def _step_summary(rows,run_count):
    episodes=[episode for _,episode in rows]
    completed=[x for x in episodes if x["state"]=="later_completion"]
    durations=[x["first_issue_to_completion_seconds"] for x in completed if x["first_issue_to_completion_seconds"] is not None]
    ids={rid for rid,_ in rows}
    return {"runs_with_issue":len(ids),"selected_runs":run_count,"episodes":len(episodes),
            "episode_states":{state:sum(x["state"]==state for x in episodes) for state in STATES},
            "failure_occurrences":sum(x["failure_occurrences"] for x in episodes),
            "handoff_occurrences":sum(x["handoff_occurrences"] for x in episodes),
            "runs_with_later_completion":len({rid for rid,x in rows if x["state"]=="later_completion"}),
            "runs_with_unresolved_episode":len({rid for rid,x in rows if x["state"]!="later_completion"}),
            "median_first_issue_to_later_completion_seconds":median(durations) if durations else None,
            "completion_durations_available":len(durations),
            "inconsistent_completion_timestamp_count":sum(x["timestamp_order_inconsistent"] for x in completed)}


def summarize(deployment_id,runs,info,settings):
    """Bounded complete retained snapshot; exact hashes include entire run records."""
    by_pattern=defaultdict(list)
    by_step=defaultdict(list)
    by_transition=defaultdict(list)
    details=[]
    all_episodes=[]
    pins=[]
    for run in runs:
        result=analyze_run(run)
        rid=run["run_id"]
        pin=e._digest({"deployment_id":deployment_id,"run":run})
        pins.append([rid,pin])
        episodes=result["episodes"]
        flags={"has_issue":bool(episodes),"no_issue":not episodes,
               "later_completion":any(x["state"]=="later_completion" for x in episodes),
               "unresolved_episode":any(x["state"]!="later_completion" for x in episodes),
               "succeeded_with_issue":run["status"]=="succeeded" and bool(episodes)}
        row={"reference":{"deployment_id":deployment_id,"run_id":rid,"run_sha256":pin},
             "started_at":run["started_at"],"finished_at":run["finished_at"],"status":run["status"],
             "reported_spec_sha256":run["reported_spec_sha256"],"reported_step_count":len(run["steps"]),
             "pattern_sha256":result["pattern_sha256"],"issue_sequence":result["issue_sequence"],
             "episodes":episodes,"flags":flags}
        details.append(row)
        by_pattern[row["pattern_sha256"]].append(row)
        for episode in episodes:
            by_step[episode["step_id"]].append((rid,episode));all_episodes.append((rid,episode))
        for (left,right),count in result["transition_counts"].items():
            by_transition[(left,right)].append((rid,count))
        if max(len(by_pattern),len(by_step),len(by_transition))>MAX_GROUPS:
            raise m.MonitoringError("issue pattern group limit exceeded; narrow the review")
    n=len(runs)
    patterns=[]
    for key,rows in by_pattern.items():
        patterns.append({"pattern_sha256":key,"issue_sequence":rows[0]["issue_sequence"],"runs":len(rows),
                         "run_status_counts":_counts(r["status"] for r in rows),
                         "runs_with_later_completion":sum(r["flags"]["later_completion"] for r in rows),
                         "runs_with_unresolved_episode":sum(r["flags"]["unresolved_episode"] for r in rows),
                         "share_of_selected_runs":len(rows)/n})
    patterns.sort(key=lambda r:(-r["runs"],r["pattern_sha256"]))
    steps=[{"step_id":key,**_step_summary(rows,n)} for key,rows in by_step.items()]
    steps.sort(key=lambda r:(-r["runs_with_issue"],r["step_id"]))
    transitions=[{"transition_sha256":e._digest([list(a),list(b)]),"from_issue":list(a),"to_issue":list(b),
                  "runs":len(rows),"occurrences":sum(count for _,count in rows),
                  "share_of_selected_runs":len(rows)/n}
                 for (a,b),rows in by_transition.items()]
    transitions.sort(key=lambda r:(-r["runs"],-r["occurrences"],r["transition_sha256"]))
    basis={"format":FORMAT,"deployment_id":deployment_id,"reported_spec_sha256":settings.reported_spec_sha256,
           "start_date":settings.start_date,"end_date":settings.end_date,"attached_design_sha256":info["design_sha256"],
           "run_pins":sorted(pins)}
    pin=e._digest(basis)
    if settings.expected_population_sha256 and settings.expected_population_sha256!=pin:
        raise m.MonitoringError("retained recovery evidence changed; reopen the population")
    if settings.view!="runs" and (settings.run_filter!="all" or settings.pattern_sha256 or settings.step_id or settings.transition_sha256):
        raise m.MonitoringError("run filters apply only to the exact run evidence view")
    if sum(x is not None for x in (settings.pattern_sha256,settings.step_id,settings.transition_sha256))>1:
        raise m.MonitoringError("choose at most one exact issue group")
    if settings.pattern_sha256 and settings.pattern_sha256 not in by_pattern:
        raise m.MonitoringError("selected issue pattern is not in this population")
    if settings.step_id and settings.step_id not in by_step:
        raise m.MonitoringError("selected issue step is not in this population")
    selected_transition=next((x for x in transitions if x["transition_sha256"]==settings.transition_sha256),None)
    if settings.transition_sha256 and selected_transition is None:
        raise m.MonitoringError("selected issue transition is not in this population")
    def matches(r):
        if settings.run_filter!="all" and not r["flags"][settings.run_filter]:return False
        if settings.pattern_sha256 and r["pattern_sha256"]!=settings.pattern_sha256:return False
        if settings.step_id and not any(x["step_id"]==settings.step_id for x in r["episodes"]):return False
        if selected_transition:
            pair=[selected_transition["from_issue"],selected_transition["to_issue"]]
            if not any([a,b]==pair for a,b in zip(r["issue_sequence"],r["issue_sequence"][1:])):return False
        return True
    population={"patterns":patterns,"steps":steps,"transitions":transitions,
                "runs":sorted((r for r in details if matches(r)),key=lambda r:(r["started_at"],r["reference"]["run_id"]))}[settings.view]
    page=population[settings.offset:settings.offset+settings.limit]
    return {"format":FORMAT,"deployment_id":deployment_id,"population_sha256":pin,
            "reported_spec_sha256":settings.reported_spec_sha256,"unknown_spec_attribution_selected":settings.reported_spec_sha256 is None,
            "start_date":settings.start_date,"end_date":settings.end_date,
            "summary":{"runs":n,"reported_steps":sum(len(r["steps"]) for r in runs),
                       "runs_without_reported_steps":sum(not r["steps"] for r in runs),
                       "run_status_counts":_counts(r["status"] for r in runs),
                       "run_flag_counts":{k:sum(r["flags"][k] for r in details) for k in ("has_issue","no_issue","later_completion","unresolved_episode","succeeded_with_issue")},
                       "patterns":len(patterns),"distinct_issue_steps":len(steps),"distinct_issue_transitions":len(transitions),
                       "episode_summary":_step_summary(all_episodes,n)},
            "page":{"view":settings.view,"run_filter":settings.run_filter,"pattern_sha256":settings.pattern_sha256,
                    "step_id":settings.step_id,"transition_sha256":settings.transition_sha256,"offset":settings.offset,
                    "limit":settings.limit,"matching":len(population),"rows":page,
                    "next_offset":settings.offset+len(page) if page and settings.offset+len(page)<len(population) else None},
            "complete_bounded_population":True,"retry_identity_verified":False,"recovery_verified":False,
            **e.CLAIMS,
            "interpretation":"An issue episode groups reported failures/handoffs of one step ID until a later reported completion of that ID in sequence order. "
                "A later completion is not proof of retry, incident resolution or successful business outcome. Consecutive issues ignore intervening nonissue steps. "
                "Repeated occurrences and distinct runs are counted separately. Unknown-version reports may mix versions. No action is taken."}


def review(deployment_id,payload):
    s=e._parse(Request,payload)
    if (s.offset or s.pattern_sha256 or s.step_id or s.transition_sha256) and not s.expected_population_sha256:
        raise m.MonitoringError("review the first population before requesting evidence")
    try:
        start,end=datetime.fromisoformat(s.start_date),datetime.fromisoformat(s.end_date)
    except ValueError:
        raise m.MonitoringError("select valid UTC dates") from None
    if not timedelta(0)<end-start<=timedelta(days=366):
        raise m.MonitoringError("select a positive UTC window of at most 366 days")
    with m._LOCK:
        conn=m._connect()
        try:
            info=m._check(conn,deployment_id)
            where="deployment_id=? AND started_at>=? AND started_at<? AND reported_spec_sha256 IS NOT DISTINCT FROM ?"
            args=[deployment_id,start,end,s.reported_spec_sha256]
            rows=conn.execute("SELECT "+e._RUN_COLUMNS+" FROM runs WHERE "+where+" ORDER BY started_at,run_id LIMIT ?",args+[MAX_RUNS+1]).fetchall()
            if len(rows)>MAX_RUNS:raise m.MonitoringError("too many retained runs; narrow the review")
            conn.execute("CREATE TEMP TABLE selected_recovery_runs AS SELECT run_id,deployment_id FROM runs WHERE "+where,args)
            steps=conn.execute("""SELECT s.run_id,s.seq,s.step_id,s.activity,s.status,s.observed_at
                FROM steps s JOIN selected_recovery_runs r ON s.deployment_id=r.deployment_id AND s.run_id=r.run_id
                ORDER BY s.run_id,s.seq LIMIT ?""",[MAX_STEPS+1]).fetchall()
            if len(steps)>MAX_STEPS:raise m.MonitoringError("too many retained steps; narrow the review")
            grouped=defaultdict(list)
            for rid,*step in steps:grouped[rid].append(step)
            runs=[e._run(row,grouped[row[0]]) for row in rows]
        finally:conn.close()
    result=summarize(deployment_id,runs,info,s)
    if len(json.dumps(result,ensure_ascii=True,separators=(",",":")).encode())>2*1024*1024:
        raise m.MonitoringError("recovery evidence page exceeds 2 MiB; choose a smaller page")
    return result
