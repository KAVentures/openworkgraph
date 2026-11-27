"""Freeze a decision protocol before execution and assemble minimized pilot review.

This is a local, externally pinned declaration. It does not authenticate
reviewers or independently establish time, causality or business outcomes.
"""
import argparse
from pathlib import Path
from . import prospective_pilot_cohort as cohort
from . import pilot_workspace as workspace
from . import pilot_decision_bounds as sensitivity

FORMAT="owg.pilot-decision-protocol.v1"
REPORT="owg.pilot-decision-evidence.v1"


def freeze(path,thresholds,*,expected_head_sha256,confirmed):
    if confirmed is not True:
        raise ValueError("explicit prospective decision protocol confirmation required")
    state=workspace.status(path,expected_head_sha256=expected_head_sha256)
    if state["revision"]!=0 or state["ledger"]["events"]:
        raise ValueError("decision policy must be frozen before first workspace mutation")
    body={"format":FORMAT,"initial_head_sha256":state["head_sha256"],
          "plan_sha256":state["plan"]["plan_sha256"],
          "schedule_sha256":state["schedule"]["schedule_sha256"],
          "thresholds":sensitivity.policy(thresholds)}
    return {**body,"protocol_sha256":cohort.digest(body)}


def verify_protocol(protocol,expected,initial,plan,schedule):
    cohort.exact(protocol,("format","initial_head_sha256","plan_sha256","schedule_sha256",
                          "thresholds","protocol_sha256"))
    body={k:v for k,v in protocol.items() if k!="protocol_sha256"}
    if (protocol["format"]!=FORMAT or cohort.pin(expected)!=protocol["protocol_sha256"]
            or cohort.digest(body)!=expected or protocol["initial_head_sha256"]!=initial
            or protocol["plan_sha256"]!=plan or protocol["schedule_sha256"]!=schedule):
        raise ValueError("prospective protocol or independent pin mismatch")
    return sensitivity.policy(protocol["thresholds"])


def report(path,protocol,truth,batches,*,expected_protocol_sha256,expected_head_sha256,confirmed):
    if confirmed is not True:
        raise ValueError("explicit private pilot decision review required")
    state=workspace.status(path,expected_head_sha256=expected_head_sha256)
    history=workspace.history(path,expected_head_sha256=expected_head_sha256)
    initial=history["history"][0]["head_sha256"]
    p=verify_protocol(protocol,expected_protocol_sha256,initial,state["plan"]["plan_sha256"],
                      state["schedule"]["schedule_sha256"])
    enrolled=cohort._verify(state["plan"],state["plan"]["plan_sha256"])
    generated=[]
    def observe(observations,coverage):
        if generated:raise ValueError("multiple scoring populations")
        summary=sensitivity.analyze(enrolled,observations,p)
        if (summary["enrolled"]!=coverage["enrolled"] or
                summary["complete_paired_episodes"]!=coverage["complete_paired_episodes"]):
            raise ValueError("decision coverage does not match canonical scoring")
        # Independent check against the canonical full-population bounds.
        from fractions import Fraction
        for arm in cohort.ARMS:
            for field in cohort.OUTCOMES:
                r=coverage["bounds"][arm][field]
                expected=sensitivity.bounds(Fraction(r["lower_numerator"],r["denominator"]),
                                            Fraction(r["upper_numerator"],r["denominator"]))
                if summary["rates"][arm][field]!=expected:
                    raise ValueError("decision arm bounds differ")
        for pair,fields in coverage["contrasts"].items():
            for field,r in fields.items():
                expected=sensitivity.bounds(Fraction(r["lower_numerator"],r["denominator"]),
                                            Fraction(r["upper_numerator"],r["denominator"]))
                if summary["contrasts"][pair][field]["episode_weighted"]!=expected:
                    raise ValueError("decision contrast bounds differ")
        generated.append(summary)
    reconciled=workspace.reconcile(path,truth,batches,expected_head_sha256=expected_head_sha256,
                                  confirmed=True,_observe=observe)
    if len(generated)!=1:
        raise ValueError("canonical scoring population unavailable")
    summary=generated[0];result=reconciled["result"];score=result["score"]
    if score["canonical_scorer_invoked"]!=summary["full_paired_coverage"]:
        raise ValueError("canonical coverage inconsistency")
    # Recheck the accepted head after potentially expensive adjudication.
    workspace.status(path,expected_head_sha256=expected_head_sha256)
    body={"format":REPORT,"protocol_sha256":expected_protocol_sha256,
          "initial_head_sha256":initial,"head_sha256":expected_head_sha256,
          "plan_sha256":state["plan"]["plan_sha256"],"schedule_sha256":state["schedule"]["schedule_sha256"],
          "reconciliation_sha256":reconciled["report_sha256"],"score_sha256":score["score_sha256"],
          "coverage_sha256":score["coverage"]["coverage_sha256"],
          "thresholds":p,"summary":summary,
          "active_batches":reconciled["active_batches"],"submitted_batches":reconciled["submitted_batches"],
          "reviewed_attempts":result["reviewed_attempts"],
          "canonical_scorer_invoked":score["canonical_scorer_invoked"],
          "decision_thresholds_met":summary["all_thresholds_met"],
          "canonical_score_and_protocol_both_available":score["canonical_scorer_invoked"],
          "head_checked_at_finish":True,
          "independent_protocol_freeze_time_verified":False,
          "independent_transport_verified":False,"reviewer_identity_verified":False,
          "blinding_verified":False,"product_effectiveness_verified":False,"rollout_authorized":False,
          "limitations":["Exact declared cohort only; not a confidence interval or a representative sampling claim.",
                         "Equal-team weighting and leave-one-team-out envelopes are descriptive sensitivity checks.",
                         "Unknown outcomes are not imputed. Full coverage is required even if bounds meet every rate threshold.",
                         "Externally retained pins are essential; the protocol does not authenticate when a file was created.",
                         "Thresholds are separate from the canonical scorer and do not authorize deployment.",
                         "The head was checked before returning, not locked against future changes."]}
    return {**body,"decision_sha256":cohort.digest(body)}


def verify_report(value,expected):
    """Check integrity of a minimized report, not independent outcome truth."""
    if not isinstance(value,dict) or value.get("format")!=REPORT:
        raise ValueError("invalid pilot decision report")
    if cohort.pin(expected)!=value.get("decision_sha256") or cohort.digest({
        k:v for k,v in value.items() if k!="decision_sha256"})!=expected:
        raise ValueError("decision report digest mismatch")
    return {"decision_sha256":expected,"self_consistency_verified":True,
            "independent_outcomes_verified":False,"rollout_authorized":False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    f=sub.add_parser("freeze")
    f.add_argument("--thresholds",type=Path,required=True)
    r=sub.add_parser("report")
    r.add_argument("--protocol",type=Path,required=True);r.add_argument("--protocol-sha256",required=True)
    r.add_argument("--episodes",type=Path,required=True);r.add_argument("--batches",type=Path,required=True)
    v=sub.add_parser("verify")
    v.add_argument("--report",type=Path,required=True);v.add_argument("--decision-sha256",required=True)
    for cmd in (f,r):
        cmd.add_argument("--db",type=Path,required=True);cmd.add_argument("--head-sha256",required=True)
    for cmd in (f,r,v):
        cmd.add_argument("--out",type=Path,required=True);cmd.add_argument("--confirm",action="store_true",required=True)
    a=parser.parse_args(argv)
    try:
        if a.out.exists():raise ValueError("exclusive new output required")
        if a.command=="freeze":
            result=freeze(a.db,cohort._load(a.thresholds),expected_head_sha256=a.head_sha256,confirmed=a.confirm)
        elif a.command=="report":
            result=report(a.db,cohort._load(a.protocol),cohort._load_jsonl(a.episodes),cohort._load(a.batches),
                expected_protocol_sha256=a.protocol_sha256,expected_head_sha256=a.head_sha256,confirmed=a.confirm)
        else:
            result=verify_report(cohort._load(a.report),a.decision_sha256)
        cohort._write(a.out,result)
    except (ValueError,TypeError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Pilot decision operation refused: invalid evidence, pin, policy or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
