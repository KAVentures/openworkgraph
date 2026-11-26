"""Read-only local workforce portfolio across retained R26 monitoring journals.

A bounded installation-local inventory, not an organization/tenant-wide
data source. Never combine currencies, count duplicate declared cohorts,
or turn disjoint pilot arm totals into realized savings.
"""
from __future__ import annotations

from collections import defaultdict, Counter
from fractions import Fraction
import json
import time

from shared.agent_procurement_portfolio import _pair, _sum
from shared.agent_procurement_router_rehearsal import _money_bounds, _rational
from . import agent_procurement_monitor_journal as journal
from . import db

FORMAT="openworkgraph.procurement-workforce-overview.v1"
ZERO=(Fraction(0),Fraction(0))
ARMS=("incumbent","challenger")
METRICS=("success","critical","forbidden","manual_leakage","route_deviation","fallback")


def _scope(key):
    return {"workflow_id":key[0],"revision":key[1],"monitor_id":key[2]}


def _tokens(state):
    plan=state.get("monitor_plan")
    if not isinstance(plan,dict):
        return None
    cases={s["case_token_sha256"] for w in plan["windows"] for s in w["plan"]["slots"]}
    sources={o["source_evidence_sha256"] for w in state["snapshot"]["windows"] for o in w["observations"]}
    # Initial pilot references are intentionally not included: multiple
    # monitors can share the same initial baseline without counting it.
    return cases,sources


def _overlaps(records):
    indexes=[defaultdict(set),defaultdict(set)]
    for key,state,_ in records:
        tokens=_tokens(state)
        if tokens is None:continue
        for index,values in zip(indexes,tokens):
            for value in values:index[value].add(key)
    result=defaultdict(set)
    for reason,index in zip(("case_token_overlap","source_reference_overlap"),indexes):
        for scopes in index.values():
            if len(scopes)>1:
                for scope in scopes:result[scope].add(reason)
    return result


def _arm_zero():
    return {"cases":0,"recorded":0,"terminal":0,
        "counts":{k:{"lower":0,"upper":0} for k in METRICS},
        "attribution_counts":{"pass":0,"fail":0,"unknown":0},"cost":ZERO}


def _add_arm(dst,src):
    for k in ("cases","recorded","terminal"):dst[k]+=src[k]
    for k in METRICS:
        for edge in ("lower","upper"):dst["counts"][k][edge]+=src["counts"][k][edge]
    for k in dst["attribution_counts"]:dst["attribution_counts"][k]+=src["attribution_counts"][k]
    dst["cost"]=_sum(dst["cost"],_pair(src["variable_cost"]))


def _finish_arm(a):
    cost=a.pop("cost");n=a["cases"]
    a["variable_cost"]=_money_bounds(cost)
    a["mean_variable_cost"]=_money_bounds(tuple(x/n if x is not None and n else None for x in cost))
    a["success_rate"]=_rational(tuple(Fraction(a["counts"]["success"][k],n) if n else None for k in ("lower","upper")))
    return a


def _row(key,state,history,current,now,overlap):
    view=journal._view(key,state,history);report=state["report"]
    plan=state.get("monitor_plan")
    reasons=[]
    if current is None:reasons.append("missing_current_process")
    elif current["revision"]!=key[1]:reasons.append("historical_process_revision")
    if plan is None:reasons.append("unavailable_cohort_identity")
    reasons.extend(sorted(overlap))
    if report["as_of_ms"]>now:reasons.append("future_snapshot_cutoff")
    due=[]
    if plan is not None:
        delay=plan["request"]["maximum_reporting_delay_ms"]
        for w in report["windows"]:
            if now>=w["end_exclusive_ms"]+delay and w["state"]!="complete_pass" and w["state"]!="rollback_review":
                due.append(w["window_id"])
    sticky=view["sticky_rollback_triggers"]
    if sticky or view["rollback_referral_recorded"]:priority="rollback_review"
    elif due or report["recommended_review"]=="rollback_review":priority="attention"
    elif reasons:priority="excluded_review"
    elif view["eligible_for_continued_monitoring_review"]:priority="continuation_review"
    else:priority="monitoring"
    return {**_scope(key),"title":current["title"] if current else key[0],
        "current_revision":current["revision"] if current else None,
        "priority":priority,"exclusion_reasons":reasons,"included_in_totals":not reasons,
        "head_sha256":view["head_sha256"],"plan_sha256":view["plan_sha256"],
        "currency":report["currency"],"as_of_ms":report["as_of_ms"],
        "last_recorded_at_ms":history[-1]["recorded_at_ms"],
        "scheduled_cases":report["scheduled_cases"],"started_window_cases":report["started_window_cases"],
        "recorded_cases":report["recorded_cases"],
        "credential_authenticated_observations":view["credential_authenticated_observations"],
        "finance_reconciled_windows":[r["window_id"] for r in state.get("finance_reconciliations",[])],
        "sticky_rollback_triggers":sticky,"rollback_referral_recorded":view["rollback_referral_recorded"],
        "overdue_window_history":view["overdue_window_history"],"due_for_review_windows":due,
        "clock_refresh_needed":bool(due and report["as_of_ms"]<now),
        "journal_eligible_for_continued_monitoring_review":view["eligible_for_continued_monitoring_review"],
        "eligible_for_continued_monitoring_review":view["eligible_for_continued_monitoring_review"] and not reasons and not due,
        "arms":report["arms"]}


def summarize(records,current,now,unreadable=()):
    # Overlap exclusion is symmetric, including historical records. The
    # operator must reconcile cohorts; do not choose a winner by timestamp.
    overlaps=_overlaps(records)
    rows=[_row(k,s,h,current.get(k[0]),now,overlaps[k]) for k,s,h in records]
    totals={}
    for row in rows:
        if not row["included_in_totals"]:continue
        currency=row["currency"]
        group=totals.setdefault(currency,{"currency":currency,"journals":0,"scheduled_cases":0,
            "started_window_cases":0,"recorded_cases":0,"credential_authenticated_observations":0,
            "arms":{arm:_arm_zero() for arm in ARMS}})
        group["journals"]+=1
        for k in ("scheduled_cases","started_window_cases","recorded_cases","credential_authenticated_observations"):
            group[k]+=row[k]
        for arm in ARMS:
            if arm in row["arms"]:_add_arm(group["arms"][arm],row["arms"][arm])
    for g in totals.values():
        g["arms"]={k:_finish_arm(v) for k,v in g["arms"].items()}
    ranks={"rollback_review":0,"attention":1,"excluded_review":2,"monitoring":3,"continuation_review":4}
    rows.sort(key=lambda r:(ranks[r["priority"]],r["workflow_id"],r["revision"],r["monitor_id"]))
    return {"format":FORMAT,"scope":"local_installation_only","as_of_server_ms":now,
        "journal_count":len(rows)+len(unreadable),"readable_journals":len(rows),
        "included_journals":sum(r["included_in_totals"] for r in rows),
        "excluded_journals":sum(not r["included_in_totals"] for r in rows),
        "priority_counts":dict(Counter(r["priority"] for r in rows)),
        "unreadable_journals":[_scope(k) for k in unreadable],
        "totals_may_be_incomplete":bool(unreadable or any(not r["included_in_totals"] for r in rows)),
        "currency_totals":[totals[k] for k in sorted(totals)],"journals":rows,
        "source_evidence_independently_verified":False,"cross_workflow_unique_business_cases_proven":False,
        "realized_savings_calculated":False,"execution_authorized":False,"result_persisted":False,
        "limitations":["Only retained journals on this installation are included; not an organization-wide live inventory.",
            "Exact overlapping declared case tokens or observation source references exclude all affected journals from totals. Different tokens may still represent the same business case.",
            "Historical revisions, unavailable cohort identity and future-dated snapshots are excluded from totals but remain visible for incident review.",
            "Unbounded amounts remain unbounded. Costs are declared variable costs of separate disjoint arms, not savings, forecasts or comparable workload-normalized causal effects.",
            "Deadline flags use the server clock without mutating retained checkpoints; refresh a journal to retain a new assessment."]}


def overview():
    with db.connect() as conn:
        conn.execute("BEGIN");journal._schema(conn)
        keys=[tuple(r) for r in conn.execute("""SELECT workflow_id,revision,monitor_id FROM procurement_monitor_journals
                     ORDER BY workflow_id,revision,monitor_id""").fetchall()]
        if len(keys)>journal.MAX_MONITORS:
            raise journal.JournalError("Local monitoring journal capacity exceeded",409)
        records=[];unreadable=[];current={}
        for key in keys:
            try:
                state,history=journal._read(conn,key)
                records.append((key,state,history))
            except journal.JournalError:
                unreadable.append(key)
            if key[0] not in current:
                current[key[0]]=None
                row=conn.execute("""SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?
                                     ORDER BY revision DESC LIMIT 1""",("local",key[0])).fetchone()
                if row is not None:
                    try:payload=json.loads(row[1])
                    except (ValueError,TypeError):payload={}
                    if isinstance(payload,dict) and payload.get("process_model"):
                        current[key[0]]={"revision":row[0],"title":payload.get("title",key[0])}
        return summarize(records,current,int(time.time()*1000),unreadable)
