"""R14 service-level surfaces over one bounded capacity grid.

Every deadline uses the same simulated completion samples. Unresolved arrivals
are unknown, abandoned arrivals never succeed, and overload is never qualified.
No observed SLA, case-level causal pairing, or production forecast is claimed.
"""
from __future__ import annotations

from bisect import bisect_right
from fractions import Fraction
from itertools import combinations
import math

from .event_log_workspace import EventLogWorkspaceError as Error

MAX_TARGETS=8
MAX_SHARES=6


def number(value,lo,hi,name):
    if (type(value) not in (int,float) or not lo<=value<=hi or not math.isfinite(value)):
        raise Error(f"{name} must be a finite number from {lo} to {hi}")
    return float(value)


def normalize(spec):
    if not isinstance(spec,dict) or set(spec)!={"targets","inverse_completion_shares"}:
        raise Error("Provide service targets and inverse completion shares")
    targets=spec["targets"]
    if not isinstance(targets,list) or not 1<=len(targets)<=MAX_TARGETS:
        raise Error("Choose one to eight distinct named service targets")
    normalized=[]
    seen=set()
    for target in targets:
        if not isinstance(target,dict) or set(target)!={"label","cycle_time_minutes","minimum_completion_share"}:
            raise Error("Each service target needs a label, deadline and completion share")
        label=target["label"]
        if (not isinstance(label,str) or not 1<=len(label)<=80 or label.strip()!=label
                or not label.isprintable() or label.casefold() in seen):
            raise Error("Service target labels must be distinct printable names")
        seen.add(label.casefold())
        normalized.append({"label":label,"cycle_time_minutes":number(target["cycle_time_minutes"],.001,525600,"Deadline minutes"),
            "minimum_completion_share":number(target["minimum_completion_share"],.01,1,"Minimum completion share")})
    shares=spec["inverse_completion_shares"]
    if not isinstance(shares,list) or not 1<=len(shares)<=MAX_SHARES:
        raise Error("Choose one to six inverse completion shares")
    shares=sorted(number(s,.01,1,"Inverse completion share") for s in shares)
    if len(set(shares))!=len(shares):
        raise Error("Inverse completion shares must be distinct")
    return {"targets":normalized,"inverse_completion_shares":shares}


def required_count(share,arrivals):
    fraction=Fraction(str(share))*arrivals
    return -(-fraction.numerator//fraction.denominator)


def classify(on_time,unknown,needed,overloaded):
    if overloaded:return "overloaded"
    if on_time>=needed:return "met"
    if on_time+unknown<needed:return "known_miss"
    return "indeterminate"


def inverse(samples,arrivals,unknown,share):
    """Minimum possible and guaranteed deadlines for all-arrival target share.

    None means no finite deadline can be established under that endpoint.
    At zero, unknowns are allowed to finish immediately; no event timestamps
    for unresolved cases are supplied by the existing simulator.
    """
    needed=required_count(share,arrivals)
    possible_rank=needed-unknown
    low=0.0 if possible_rank<=0 else samples[possible_rank-1] if possible_rank<=len(samples) else None
    high=samples[needed-1] if needed<=len(samples) else None
    return {"share":share,"required_completions":needed,
            "minimum_possible_seconds":low,"minimum_guaranteed_seconds":high,
            "possible_at_some_finite_deadline":low is not None,
            "guaranteed_at_some_finite_deadline":high is not None}


def summarize(run,samples,spec):
    arrivals=run["post_warmup_arrivals"]
    unknown=run["unresolved"]
    ordered=sorted(samples)
    if (arrivals<1 or unknown<0 or len(ordered)!=run["completed"] or
            len(ordered)+unknown+run["abandoned"]!=arrivals or
            any(not math.isfinite(v) or v<0 for v in ordered)):
        raise Error("Inconsistent service review accounting")
    targets=[]
    for target in spec["targets"]:
        on=bisect_right(ordered,target["cycle_time_minutes"]*60)
        needed=required_count(target["minimum_completion_share"],arrivals)
        targets.append({"label":target["label"],"on_time":on,"known_misses":arrivals-on-unknown,
            "unresolved":unknown,"required_completions":needed,
            "share_lower":on/arrivals,"share_upper":(on+unknown)/arrivals,
            "status":classify(on,unknown,needed,run["overloaded"]),
            "additional_on_time_completions_needed":max(0,needed-on),
            "can_meet_if_all_unresolved_on_time":on+unknown>=needed})
    return {"seed":run["seed"],"arrivals":arrivals,"completed":len(samples),
            "abandoned":run["abandoned"],"unresolved":unknown,"overloaded":run["overloaded"],
            "targets":targets,
            "inverse_deadlines":[inverse(ordered,arrivals,unknown,s) for s in spec["inverse_completion_shares"]]}


def cell(label,rate,runs,spec):
    targets=[]
    for i,target in enumerate(spec["targets"]):
        rows=[r["targets"][i] for r in runs]
        counts={status:sum(r["status"]==status for r in rows) for status in ("overloaded","met","known_miss","indeterminate")}
        status=("overloaded" if counts["overloaded"] else "not_met_in_all_runs" if counts["known_miss"] else
                "met_in_all_runs" if counts["met"]==len(runs) else "indeterminate")
        targets.append({**target,"status":status,"replication_status_counts":counts,
            "worst_replication_share_lower":min(r["share_lower"] for r in rows),
            "worst_replication_share_upper":min(r["share_upper"] for r in rows),
            "maximum_additional_on_time_completions_needed":max(r["additional_on_time_completions_needed"] for r in rows)})
    inverse_rows=[]
    for i,share in enumerate(spec["inverse_completion_shares"]):
        rows=[r["inverse_deadlines"][i] for r in runs]
        low=[r["minimum_possible_seconds"] for r in rows]
        high=[r["minimum_guaranteed_seconds"] for r in rows]
        inverse_rows.append({"share":share,
            "minimum_possible_deadline_across_all_runs_seconds":max(low) if all(x is not None for x in low) else None,
            "minimum_guaranteed_deadline_across_all_runs_seconds":max(high) if all(x is not None for x in high) else None,
            "all_runs_have_finite_possible_deadline":all(x is not None for x in low),
            "all_runs_have_finite_guaranteed_deadline":all(x is not None for x in high),
            "stable_all_runs":not any(r["overloaded"] for r in runs)})
    statuses={r["status"] for r in targets}
    joint=("overloaded" if "overloaded" in statuses else "not_met_in_all_runs" if "not_met_in_all_runs" in statuses else
           "met_in_all_runs" if statuses=={"met_in_all_runs"} else "indeterminate")
    return {"design":label,"arrival_rate_per_hour":rate,"joint_status":joint,"targets":targets,
            "inverse_deadlines":inverse_rows,"runs":runs}


def compare(cells,spec):
    by_design={}
    for c in cells:
        by_design.setdefault(c["design"],{})[c["arrival_rate_per_hour"]]=c
    envelopes=[]
    for design,by_rate in by_design.items():
        rates=sorted(by_rate)
        passed=[r for r in rates if by_rate[r]["joint_status"]=="met_in_all_runs"]
        other=[r for r in rates if r not in passed]
        envelopes.append({"design":design,"jointly_passing_tested_rates":passed,
            "other_tested_rates":other,"highest_jointly_passing_tested_rate":max(passed) if passed else None,
            "nonmonotonic_tested_pattern":bool(passed and other and min(other)<max(passed)),
            "per_target":[{"label":target["label"],"passing_tested_rates":[r for r in rates
                if by_rate[r]["targets"][i]["status"]=="met_in_all_runs"]} for i,target in enumerate(spec["targets"])]})
    pairwise=[]
    # Stable pairs only can demonstrate robust target-share dominance. The
    # same seed is a common simulation input, not a matched observed case.
    for left,right in combinations(by_design,2):
        for rate in sorted(by_design[left]):
            a,b=by_design[left][rate],by_design[right][rate]
            ar={r["seed"]:r for r in a["runs"]}
            br={r["seed"]:r for r in b["runs"]}
            if set(ar)!=set(br) or any(ar[s]["arrivals"]!=br[s]["arrivals"] for s in ar):
                raise Error("Service comparison seed or arrival accounting differs")
            stable=not any(r["overloaded"] for r in [*ar.values(),*br.values()])
            rows=[];all_lo=[];all_hi=[]
            for i,target in enumerate(spec["targets"]):
                bounds=[]
                for seed in sorted(ar):
                    x,y=ar[seed]["targets"][i],br[seed]["targets"][i]
                    n=ar[seed]["arrivals"]
                    lo=Fraction(y["on_time"]-x["on_time"]-x["unresolved"],n)
                    hi=Fraction(y["on_time"]+y["unresolved"]-x["on_time"],n)
                    bounds.append((lo,hi))
                lo=min(x[0] for x in bounds);hi=max(x[1] for x in bounds)
                all_lo.extend(x[0] for x in bounds);all_hi.extend(x[1] for x in bounds)
                rows.append({"label":target["label"],"right_minus_left_share_lower":float(lo),
                    "right_minus_left_share_upper":float(hi),
                    "right_strictly_more_in_every_run":stable and lo>0,
                    "left_strictly_more_in_every_run":stable and hi<0})
            direction=("overload_prevents_dominance" if not stable else
                "right_weakly_dominates_all_targets" if all(x>=0 for x in all_lo) and any(x>0 for x in all_lo) else
                "left_weakly_dominates_all_targets" if all(x<=0 for x in all_hi) and any(x<0 for x in all_hi) else
                "equal_on_all_targets" if all(x==0 for x in all_lo+all_hi) else "unresolved_or_tradeoff")
            pairwise.append({"left_design":left,"right_design":right,"arrival_rate_per_hour":rate,
                "stable_all_paired_runs":stable,"direction":direction,"targets":rows})
    return {"format":"openworkgraph.simulation-service-level-review.v1","inputs":spec,
        "cells":cells,"joint_operating_envelopes":envelopes,"pairwise_service_comparisons":pairwise,
        "single_simulation_per_original_grid_run":True,"unknown_outcomes_imputed":False,
        "bounds_are_not_confidence_intervals":True,"observed_sla_verified":False,
        "production_capacity_certified":False,"execution_authorized":False,
        "interpretation":"All targets reuse the same simulated runs. Known late completions and abandonment cannot satisfy a given deadline. "
            "Unresolved arrivals are unknown, not imputed. Inverse deadlines are all-arrival order-statistic bounds, not completed-only percentiles. "
            "A null endpoint means no finite deadline established at that endpoint. Overloaded runs never qualify. "
            "Only tested demand levels and seeds are described; no real service guarantee, causal savings or staffing recommendation."}
