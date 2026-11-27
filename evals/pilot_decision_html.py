"""Render an exact minimized pilot report for authorized offline review.

This is a noninteractive HTML artifact, not a verification of human
judgments, source outcomes or release readiness. No network resources.
"""
import argparse
from fractions import Fraction
from html import escape
from pathlib import Path
import os

from . import prospective_pilot_cohort as cohort
from . import pilot_decision as decision
from . import pilot_decision_bounds as sensitivity

MAX_HTML_BYTES=512*1024

def _int(value,lo=0,hi=500):
    if type(value) is not int or not lo<=value<=hi:
        raise ValueError("invalid bounded count")
    return value


def _fraction(value):
    cohort.exact(value,("numerator","denominator"))
    n,d=value["numerator"],value["denominator"]
    # Fractions for equal-team weighting may have large exact denominators.
    if type(n) is not int or type(d) is not int or not 1<=d or abs(n)>d or d.bit_length()>1024:
        raise ValueError("invalid identified bound")
    return Fraction(n,d)


def _percent(value):
    if value is None:return "Not established"
    cohort.exact(value,("lower","upper"))
    lo,hi=_fraction(value["lower"]),_fraction(value["upper"])
    if lo>hi:raise ValueError("reversed bound")
    # Render conservative outward rounding to hundredths of a percentage point.
    lower=lo*10000;upper=hi*10000
    a=lower.numerator//lower.denominator
    b=-((-upper.numerator)//upper.denominator)
    def fmt(x):
        return ("-" if x<0 else "")+str(abs(x)//100)+"."+str(abs(x)%100).zfill(2)+"%"
    return fmt(a) if a==b else fmt(a)+" to "+fmt(b)


def _table(headers,rows):
    return '<table><thead><tr>'+''.join('<th scope="col">'+escape(str(x))+'</th>' for x in headers)+\
        '</tr></thead><tbody>'+''.join('<tr>'+''.join(
        ('<th scope="row">'+escape(str(x))+'</th>') if i==0 else ('<td>'+escape(str(x))+'</td>')
        for i,x in enumerate(row))+'</tr>' for row in rows)+'</tbody></table>'


def render(value,expected,*,confirmed):
    if confirmed is not True:raise ValueError("explicit report rendering approval required")
    decision.verify_report(value,expected)
    s=value["summary"];p=sensitivity.policy(value["thresholds"])
    n=_int(s["enrolled"],1);teams=_int(s["teams"],1,n);complete=_int(s["complete_paired_episodes"],0,n)
    outcome=s["threshold_outcome"]
    if outcome not in ("pass","fail","indeterminate") or type(s["all_thresholds_met"]) is not bool:
        raise ValueError("invalid outcome")
    if s["all_thresholds_met"]!=(outcome=="pass") or (outcome=="pass" and complete!=n):
        raise ValueError("inconsistent decision outcome")
    rate_rows=[]
    for arm in sensitivity.ARMS:
        rate_rows.append([arm,*[_percent(s["rates"][arm][field]) for field in sensitivity.OUTCOMES]])
    contrasts=[]
    for left,right in sensitivity.COMPARISONS:
        name=right+"_vs_"+left
        for field in sensitivity.OUTCOMES:
            c=s["contrasts"][name][field]
            contrasts.append([right+" vs "+left+" · "+field.replace("_"," "),
                _percent(c["episode_weighted"]),_percent(c["equal_team_weighted"]),
                _percent(c["leave_one_team_out_envelope"])])
    gates=[]
    expected_names={"minimum_enrolled","minimum_teams","minimum_episodes_per_team","full_paired_coverage",
                    "min_goal_success_bps","max_goal_wrong_action_bps"}
    for left in ("control","raw"):
        for weighting in ("episode","equal_team"):
            for name in ("min_goal_success_delta_bps","max_goal_wrong_action_delta_bps"):
                expected_names.add("goal_vs_"+left+"_"+weighting+"_"+name)
        for name in ("min_leave_one_team_success_delta_bps","max_leave_one_team_wrong_action_delta_bps"):
            expected_names.add("goal_vs_"+left+"_leave_one_team_"+name)
    seen=set()
    for gate in s["gates"]:
        name=gate["gate"];status=gate["status"]
        if name not in expected_names or name in seen or status not in ("pass","fail","indeterminate"):
            raise ValueError("unexpected gate")
        seen.add(name)
        if "identified_bounds" in gate:
            basis=_percent(gate["identified_bounds"])
            threshold=_int(gate["threshold_bps"],-10000,10000)
            criteria=gate["direction"]
            if criteria not in ("min","max"):raise ValueError("invalid threshold direction")
            criteria+=" "+_percent({"lower":{"numerator":threshold,"denominator":10000},
                                    "upper":{"numerator":threshold,"denominator":10000}})
        elif name=="full_paired_coverage":
            basis=str(complete)+" / "+str(n);criteria="Full paired coverage"
        else:
            basis=str(_int(gate["actual"]));criteria="Minimum "+str(_int(gate["minimum"],1))
        gates.append([name.replace("_"," "),criteria,basis,status])
    if seen!=expected_names:raise ValueError("incomplete gate set")
    actual="fail" if any(r[-1]=="fail" for r in gates) else (
        "indeterminate" if any(r[-1]=="indeterminate" for r in gates) else "pass")
    if actual!=outcome:raise ValueError("inconsistent aggregate gate result")
    pins=[]
    for key in ("protocol_sha256","head_sha256","plan_sha256","schedule_sha256","reconciliation_sha256",
                "score_sha256","coverage_sha256","decision_sha256"):
        pins.append([key.replace("_"," "),cohort.pin(value[key])])
    css="""*{box-sizing:border-box}body{font:16px/1.6 system-ui,sans-serif;background:#f6f7f6;color:#17221e;margin:0}
main{max-width:1150px;margin:auto;padding:20px}section{background:white;border:1px solid #bac6bf;border-radius:10px;padding:18px;margin:18px 0}
h1,h2,h3,p,td,th{overflow-wrap:anywhere}table{width:100%;border-collapse:collapse;table-layout:fixed}td,th{text-align:left;padding:9px;vertical-align:top;border-bottom:1px solid #c8d1cc}th[scope=row]{font-weight:600}
thead th{background:#e8eeea}th:first-child{width:36%}code{overflow-wrap:anywhere} @media(max-width:600px){main{padding:10px}section{padding:10px}td,th{padding:4px;font-size:12px}} @media print{body{background:white}section{border:0;break-inside:avoid}}"""
    headline=_table(["Measure","Result"],[["Threshold review",outcome],["Enrolled episodes",n],["Declared teams",teams],
        ["Complete paired episodes",complete],["Canonical scorer invoked",str(value["canonical_scorer_invoked"] is True)],
        ["Independent outcome verification","Not established"],["Rollout authorization","Not granted"]])
    html='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+\
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'+\
        '<title>OWG pilot decision evidence</title><style>'+css+'</style></head><body><main>'+\
        '<h1>Prospective pilot decision evidence</h1><p>Private minimized review artifact. An exact declared sample, not a deployment decision.</p>'+\
        '<section><h2>Decision threshold overview</h2>'+headline+'</section>'+\
        '<section><h2>Reported outcome rates</h2>'+_table(["Arm","Continuation success","Consequential wrong action"],rate_rows)+'</section>'+\
        '<section><h2>Full-denominator sensitivity</h2><p>Contrasts are percentage-point differences. Bounds reflect unknown outcomes; removal envelopes also reflect omitted-team variation. These are not confidence intervals.</p>'+\
        _table(["Comparison","Episode weighted","Equal team weighted","Leave one team out"],contrasts)+'</section>'+\
        '<section><h2>Frozen gate results</h2><p>Every removal must satisfy its applicable stress gate. Incomplete review cannot pass.</p>'+\
        _table(["Gate","Criterion","Observed or bounded value","Outcome"],gates)+'</section>'+\
        '<section><h2>Declared policy</h2>'+_table(["Threshold","Value"],sorted(p.items()))+'</section>'+\
        '<section><h2>Exact provenance</h2>'+_table(["Pin","SHA-256"],pins)+\
        '<p>External pins establish file consistency only. No raw judgments, episode/team identifiers or transport bodies are included. A party able to replace both the report and its external pin can forge a consistent artifact. Small cohort aggregates may still be sensitive. Review disclosure independently.</p></section></main></body></html>'
    if len(html.encode("utf-8"))>MAX_HTML_BYTES:raise ValueError("report exceeds byte budget")
    return html


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--decision-sha256",required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--confirm",action="store_true",required=True)
    args=parser.parse_args(argv)
    try:
        html=render(cohort._load(args.report),args.decision_sha256,confirmed=args.confirm)
        with os.fdopen(os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"wb") as file:
            file.write(html.encode("utf-8"))
    except (ValueError,TypeError,KeyError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Pilot report rendering refused: invalid report, pin or output path.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
