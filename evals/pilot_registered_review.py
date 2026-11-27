"""Durable prospective decision registration and reviewed next-step records.

Local only. The accepted workspace head commits to a single prospective
policy before the first attempt. Decisions record human review, never
grant business execution, deployment, or release authorization.
"""
import argparse
from pathlib import Path
from . import prospective_pilot_cohort as cohort
from . import pilot_workspace as workspace
from . import pilot_decision as decision

MAX_REVIEWS=50
DISPOSITIONS=("collect_more_evidence","revise_product","continue_external_validation","stop_study")
REASONS=("incomplete_review","thresholds_not_met","thresholds_met_followup_required",
         "source_or_transport_uncertain","external_validation_required","operator_stop")
ALLOWED={
    "collect_more_evidence":{"incomplete_review","source_or_transport_uncertain","external_validation_required"},
    "revise_product":{"thresholds_not_met","source_or_transport_uncertain","external_validation_required"},
    "continue_external_validation":{"thresholds_met_followup_required"},
    "stop_study":{"operator_stop","thresholds_not_met","source_or_transport_uncertain"},
}
FORMAT="owg.registered-pilot-review.v1"


def _initial(state):
    return workspace._initial(state["plan"],state["schedule"],
                             state["plan"]["plan_sha256"],state["schedule"]["schedule_sha256"])


def validate_state(state):
    initial=_initial(state)
    protocol=state["decision_protocol"]
    decision.verify_protocol(protocol,protocol["protocol_sha256"],initial["head_sha256"],
                             state["plan"]["plan_sha256"],state["schedule"]["schedule_sha256"])
    if state["revision"]<1:
        raise ValueError("protocol registration must advance initial head")
    reviews=state["decision_reviews"]
    if not isinstance(reviews,list) or len(reviews)>MAX_REVIEWS:
        raise ValueError("invalid bounded decision review inventory")
    last=1
    for row in reviews:
        cohort.exact(row,("revision","evidence_head_sha256","decision_sha256","disposition","reason",
                          "threshold_outcome","full_paired_coverage","review_sha256"))
        rev=row["revision"]
        if type(rev) is not int or not last<rev<=state["revision"]:
            raise ValueError("invalid decision review revision")
        last=rev
        cohort.pin(row["evidence_head_sha256"]);cohort.pin(row["decision_sha256"])
        if cohort.pin(row["review_sha256"])!=cohort.digest({k:v for k,v in row.items() if k!="review_sha256"}):
            raise ValueError("decision review digest mismatch")
        if row["disposition"] not in ALLOWED or row["reason"] not in ALLOWED[row["disposition"]]:
            raise ValueError("unsupported review disposition")
        if row["threshold_outcome"] not in ("pass","fail","indeterminate") or type(row["full_paired_coverage"]) is not bool:
            raise ValueError("invalid report outcome")
        if row["threshold_outcome"]=="pass" and not row["full_paired_coverage"]:
            raise ValueError("incomplete coverage cannot pass")
        if row["disposition"]=="continue_external_validation" and row["threshold_outcome"]!="pass":
            raise ValueError("validation continuation requires passing protocol")


def validate_history(state,history):
    registration=[r for r in history if r[3]=="register_decision_protocol"]
    records=[r for r in history if r[3]=="record_decision_review"]
    if "decision_protocol" not in state or len(registration)!=1 or registration[0][0]!=1:
        raise ValueError("missing or misplaced decision protocol registration")
    initial=_initial(state)
    candidate=workspace._head({**workspace._body(initial),"revision":1,
        "previous_head_sha256":initial["head_sha256"],
        "history_sha256":workspace._history_pin(initial["history_sha256"],1,initial["head_sha256"],"register_decision_protocol"),
        "decision_protocol":state["decision_protocol"],"decision_reviews":[]})
    if registration[0][1:3]!=(initial["head_sha256"],candidate["head_sha256"]):
        raise ValueError("registered protocol differs from first accepted head")
    reviews=state["decision_reviews"]
    if len(records)!=len(reviews) or any(r[0]!=v["revision"] or r[1]!=v["evidence_head_sha256"]
                                       for r,v in zip(records,reviews)):
        raise ValueError("decision review history mismatch")


def register(path,thresholds,*,expected_head_sha256,confirmed):
    if confirmed is not True:
        raise ValueError("explicit prospective registration required")
    with workspace._transaction(path,write=True) as conn:
        state=workspace._load(conn);workspace._match(state,expected_head_sha256)
        if state["revision"]!=0 or state["ledger"]["events"]:
            raise workspace.Conflict("register exactly once before first workspace mutation")
        initial=state["head_sha256"]
        from . import pilot_decision_bounds as sensitivity
        body={"format":decision.FORMAT,"initial_head_sha256":initial,
              "plan_sha256":state["plan"]["plan_sha256"],"schedule_sha256":state["schedule"]["schedule_sha256"],
              "thresholds":sensitivity.policy(thresholds)}
        protocol={**body,"protocol_sha256":cohort.digest(body)}
        return workspace._commit(conn,state,{**workspace._body(state),"decision_protocol":protocol,
                                             "decision_reviews":[]},"register_decision_protocol")


def status(path,*,expected_head_sha256):
    state=workspace.status(path,expected_head_sha256=expected_head_sha256)
    if "decision_protocol" not in state:
        return {"format":FORMAT,"head_sha256":expected_head_sha256,"registered":False,
                "registration_possible":state["revision"]==0,"rollout_authorized":False}
    rows=state["decision_reviews"]
    latest=rows[-1] if rows else None
    return {"format":FORMAT,"head_sha256":expected_head_sha256,"registered":True,
            "protocol":state["decision_protocol"],"review_count":len(rows),
            "reviews":rows,"latest_review_current":bool(latest and latest["revision"]==state["revision"]),
            "latest_review":latest,"rollout_authorized":False}


def preview(path,truth,batches,*,expected_head_sha256,expected_protocol_sha256,confirmed):
    state=workspace.status(path,expected_head_sha256=expected_head_sha256)
    protocol=state.get("decision_protocol")
    if protocol is None or protocol["protocol_sha256"]!=cohort.pin(expected_protocol_sha256):
        raise ValueError("exact registered decision protocol required")
    return decision.report(path,protocol,truth,batches,expected_protocol_sha256=expected_protocol_sha256,
                           expected_head_sha256=expected_head_sha256,confirmed=confirmed)


def record(path,truth,batches,*,expected_head_sha256,expected_protocol_sha256,expected_decision_sha256,
           disposition,reason,confirmed):
    if confirmed is not True:
        raise ValueError("explicit reviewed next-step confirmation required")
    if disposition not in ALLOWED or reason not in ALLOWED[disposition]:
        raise ValueError("unsupported disposition/reason combination")
    # Canonical recomputation before the write transaction. The exact head is
    # then compared and swapped; any intervening action invalidates this review.
    report=preview(path,truth,batches,expected_head_sha256=expected_head_sha256,
                   expected_protocol_sha256=expected_protocol_sha256,confirmed=True)
    decision.verify_report(report,expected_decision_sha256)
    outcome=report["summary"]["threshold_outcome"]
    coverage=report["summary"]["full_paired_coverage"]
    if disposition=="continue_external_validation" and (outcome!="pass" or not coverage):
        raise ValueError("passing complete prospective protocol required")
    if reason=="incomplete_review" and coverage:
        raise ValueError("incomplete review reason contradicts report")
    if reason=="thresholds_not_met" and outcome!="fail":
        raise ValueError("failed threshold reason contradicts report")
    with workspace._transaction(path,write=True) as conn:
        state=workspace._load(conn);workspace._match(state,expected_head_sha256)
        if state["decision_protocol"]["protocol_sha256"]!=expected_protocol_sha256:
            raise workspace.Conflict("registered protocol changed")
        rows=state["decision_reviews"]
        if len(rows)>=MAX_REVIEWS:
            raise ValueError("decision review capacity reached")
        if rows and rows[-1]["revision"]==state["revision"]:
            raise workspace.Conflict("latest review already current; new study evidence required")
        row={"revision":state["revision"]+1,"evidence_head_sha256":expected_head_sha256,
             "decision_sha256":expected_decision_sha256,"disposition":disposition,"reason":reason,
             "threshold_outcome":outcome,"full_paired_coverage":coverage}
        row["review_sha256"]=cohort.digest(row)
        updated=workspace._commit(conn,state,{**workspace._body(state),"decision_reviews":[*rows,row]},
                                  "record_decision_review")
        return {"format":FORMAT,"head_sha256":updated["head_sha256"],"protocol_sha256":expected_protocol_sha256,
                "review":row,"latest_review_current":True,"rollout_authorized":False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    reg=sub.add_parser("register");reg.add_argument("--thresholds",type=Path,required=True)
    st=sub.add_parser("status")
    pre=sub.add_parser("preview");rec=sub.add_parser("record")
    for cmd in (pre,rec):
        cmd.add_argument("--protocol-sha256",required=True)
        cmd.add_argument("--episodes",type=Path,required=True)
        cmd.add_argument("--batches",type=Path,required=True)
    rec.add_argument("--decision-sha256",required=True)
    rec.add_argument("--disposition",choices=DISPOSITIONS,required=True)
    rec.add_argument("--reason",choices=REASONS,required=True)
    for cmd in (reg,st,pre,rec):
        cmd.add_argument("--db",type=Path,required=True)
        cmd.add_argument("--head-sha256",required=True)
        cmd.add_argument("--out",type=Path,required=True)
        cmd.add_argument("--confirm",action="store_true",required=True)
    a=parser.parse_args(argv)
    try:
        if a.out.exists() or a.out.resolve()==a.db.resolve():
            raise ValueError("exclusive new output required")
        kw={"expected_head_sha256":a.head_sha256}
        if a.command=="register":
            state=register(a.db,cohort._load(a.thresholds),confirmed=a.confirm,**kw)
            result={"format":FORMAT,"head_sha256":state["head_sha256"],"protocol":state["decision_protocol"],
                    "registered":True,"rollout_authorized":False}
        elif a.command=="status":
            result=status(a.db,**kw)
        else:
            args=(a.db,cohort._load_jsonl(a.episodes),cohort._load(a.batches))
            kw.update(expected_protocol_sha256=a.protocol_sha256,confirmed=a.confirm)
            if a.command=="preview":
                result=preview(*args,**kw)
            else:
                result=record(*args,expected_decision_sha256=a.decision_sha256,
                              disposition=a.disposition,reason=a.reason,**kw)
        cohort._write(a.out,result)
    except (ValueError,TypeError,KeyError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Registered pilot review refused: invalid evidence, pin, state or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
