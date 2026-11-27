"""Local exact intervention case-mix review and pinned case evidence."""
from datetime import datetime,timedelta
from pydantic import ValidationError

from . import event_log_impact as impact
from . import event_log_workspace as workspace
from . import event_log_case_mix_math as math

Error=workspace.EventLogWorkspaceError


def _parse(payload,evidence):
    keys={"analysis","case_mix"}
    if evidence:keys|={"expected_review_sha256","stratum_sha256","group","period","after","limit"}
    if type(payload) is not dict or set(payload)-keys or not {"analysis","case_mix"}<=set(payload):
        raise Error("provide explicit impact analysis and case mix settings")
    try:settings=math.Settings.model_validate(payload["case_mix"])
    except ValidationError:raise Error("invalid bounded case mix settings") from None
    parsed=impact.parse(payload["analysis"])
    if parsed.time_profile is not None:raise Error("case mix review is separate from time profile review")
    if evidence:
        for k in ("expected_review_sha256","stratum_sha256"):
            v=payload.get(k)
            if type(v) is not str or len(v)!=64 or any(c not in "0123456789abcdef" for c in v):
                raise Error("case evidence requires exact review and stratum digests")
        if payload.get("group") not in ("all","treated","control") or payload.get("period") not in math.PERIODS:
            raise Error("choose an exact group and period")
        limit=payload.get("limit",30)
        after=payload.get("after")
        if type(limit) is not int or not 1<=limit<=50 or after is not None and (type(after) is not str or not 1<=len(after)<=2000):
            raise Error("invalid bounded evidence page")
    return parsed,settings


def _labels(conn,dimension,groups):
    if dimension=="utc_start_weekday":
        return {key:("observed",str(started.isoweekday())) for rows in groups.values() for key,started,_,_ in rows}
    col="activity" if dimension=="first_activity" else "resource"
    rows=conn.execute(f"""SELECT c.case_id,count(DISTINCT nullif(e.{col},'')),
            count(*) FILTER (WHERE e.{col} IS NULL OR e.{col}=''),min(nullif(e.{col},''))
        FROM impact_cases c JOIN ce e ON e.case_id=c.case_id AND e.ts=c.started GROUP BY c.case_id""").fetchall()
    return {key:("missing",None) if distinct==0 else ("ambiguous",None) if distinct!=1 or missing else ("observed",label)
            for key,distinct,missing,label in rows}


def _run(log_id,payload,evidence):
    parsed,settings=_parse(payload,evidence)
    at=datetime.fromisoformat(parsed.intervention_at.replace("Z","+00:00")).replace(tzinfo=None)
    window=timedelta(days=parsed.window_days) if parsed.window_days else None
    def run(conn):
        groups,first,start,end,recent,last,count=impact._population(conn,log_id,parsed,at,window,max_cases=math.MAX_CASES)
        labels=_labels(conn,settings.dimension,groups)
        basis={"log_id":log_id,"analysis":parsed.model_dump(mode="json"),"visible_first":first.isoformat(),
               "visible_last":last.isoformat(),"recent":recent.isoformat() if recent else None}
        report,inventory=math.calculate(groups,labels,settings,at,start,end,recent,parsed.exclude_spanning,impact._case_period,basis,count)
        report.update(log_id=log_id,analysis=basis["analysis"],unit=impact.UNITS[parsed.metric])
        if not evidence:return report
        if report["review_sha256"]!=payload["expected_review_sha256"]:
            raise Error("case mix evidence changed; rerun the review")
        group,period=payload["group"],payload["period"]
        if group not in groups:raise Error("group does not match the reviewed analysis")
        matches=[row for row in report["strata"] if row["stratum_sha256"]==payload["stratum_sha256"]]
        if len(matches)!=1:raise Error("stratum is not in the reviewed population")
        label=(matches[0]["label_kind"],matches[0]["label"])
        rows=inventory[(label,(group,period))]
        after=payload.get("after");limit=payload.get("limit",30)
        page=[r for r in rows if after is None or r[0]>after][:limit+1]
        return {"review_sha256":report["review_sha256"],"stratum_sha256":payload["stratum_sha256"],
                "group":group,"period":period,"selected_cases":len(rows),"unit":report["unit"],
                "cases":[{"case_id":key,"started_at":a.isoformat()+"Z","ended_at":b.isoformat()+"Z","value":v} for key,a,b,v in page[:limit]],
                "next_after":page[limit-1][0] if len(page)>limit else None}
    if not math.GUARD.acquire(blocking=False):raise Error("another local case mix review is running")
    try:
        try:return workspace._query(log_id,parsed.case_type,run)
        except ValueError as exc:
            if isinstance(exc,Error):raise
            raise Error(str(exc)) from None
    finally:math.GUARD.release()


def review(log_id,payload):
    return _run(log_id,payload,False)


def evidence(log_id,payload):
    return _run(log_id,payload,True)
