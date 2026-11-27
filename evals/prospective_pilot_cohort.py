"""Private prospective pilot denominator and paired outcome bounds (R25).

No capture, model calls, source reads, or participant identity verification.
Keep the independently obtained plan digest outside the results directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

ARMS = ("control", "raw", "goal")
OUTCOMES = ("continuation_success", "consequential_wrong_action")
MISSING = ("not_started", "withdrawn", "source_unavailable", "model_failed",
           "review_pending", "review_unavailable", "other")
MAX_EPISODES = 500
MAX_BYTES = 32 * 1024 * 1024
FORMAT = "owg.prospective-pilot-cohort.v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("unexpected or missing fields")


def token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("use bounded opaque identifiers")
    return value


def pin(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("expected lowercase SHA-256")
    return value


def _index(rows, fields=None):
    if not isinstance(rows, list) or len(rows) > MAX_EPISODES:
        raise ValueError("invalid bounded episode list")
    result = {}
    for row in rows:
        if fields is not None:
            exact(row, fields)
        if not isinstance(row, dict):
            raise ValueError("episode object required")
        eid = token(row.get("episode_id"))
        if eid in result:
            raise ValueError("duplicate episode")
        result[eid] = row
    return result


def freeze(roster, *, confirmed):
    """Freeze a declared roster without retaining prompts or ground-truth labels."""
    if confirmed is not True:
        raise ValueError("explicit prospective cohort confirmation required")
    rows = _index(roster, ("episode_id", "team_id", "model_id", "source_state_id", "truth_sha256"))
    if not rows:
        raise ValueError("empty cohort")
    for row in rows.values():
        for key in ("team_id", "model_id", "source_state_id"):
            token(row[key])
        pin(row["truth_sha256"])
    body = {"format": FORMAT, "episodes": [dict(rows[k]) for k in sorted(rows)],
            "arms": list(ARMS)}
    return {**body, "plan_sha256": digest(body)}


def prepare(truth, assignments, *, confirmed):
    """Build the frozen plan from existing canonical episode JSONL records."""
    rows = _index(truth)
    assigned = _index(assignments, ("episode_id","team_id","model_id","source_state_id"))
    if not rows or set(rows)!=set(assigned):
        raise ValueError("assignment must cover exact episode roster")
    return freeze([{**assigned[k],"truth_sha256":digest(rows[k])} for k in rows],
                  confirmed=confirmed)


def disposition_template(plan, *, expected_plan_sha256, confirmed):
    """Private worklist; all rows start explicitly not started."""
    if confirmed is not True:
        raise ValueError("explicit template confirmation required")
    enrolled=_verify(plan,expected_plan_sha256)
    return [{"episode_id":eid,"arm":a,"status":"not_started"} for eid in sorted(enrolled) for a in ARMS]


def _verify(plan, expected):
    exact(plan, ("format", "episodes", "arms", "plan_sha256"))
    if (pin(expected) != plan["plan_sha256"] or
            freeze(plan["episodes"], confirmed=True) != plan):
        raise ValueError("frozen cohort or external pin mismatch")
    return _index(plan["episodes"])


def _bounds(known, population):
    positives = sum(known)
    return {"observed_positive": positives, "reviewed": len(known),
            "unknown": population-len(known), "denominator": population,
            "lower_numerator": positives, "upper_numerator": positives+population-len(known)}


def _delta(left, right, population):
    # Sharp marginal contrast bounds: each unobserved Boolean may be 0 or 1.
    low = sum(int(right[k]) if k in right else 0 for k in population)
    high = sum(int(right[k]) if k in right else 1 for k in population)
    lo_left = sum(int(left[k]) if k in left else 0 for k in population)
    hi_left = sum(int(left[k]) if k in left else 1 for k in population)
    return {"lower_numerator": low-hi_left, "upper_numerator": high-lo_left,
            "denominator": len(population), "paired_reviewed": len(set(left)&set(right))}


def inspect(plan, observations, *, expected_plan_sha256, confirmed):
    """Coverage and exact binary bounds over ALL enrolled episodes.

    Every observation is a declared disposition, not independent evidence.
    Missing rows remain unknown; withdrawal never shrinks the denominator.
    """
    if confirmed is not True:
        raise ValueError("explicit private review confirmation required")
    enrolled = _verify(plan, expected_plan_sha256)
    if not isinstance(observations, list) or len(observations) > len(enrolled)*len(ARMS):
        raise ValueError("invalid observation list")
    cells = {}
    reviewed = {arm: {field: {} for field in OUTCOMES} for arm in ARMS}
    for row in observations:
        if not isinstance(row, dict):
            raise ValueError("observation object required")
        status = row.get("status")
        fields = ("episode_id", "arm", "status")
        if status == "reviewed":
            exact(row, (*fields, "model_id", "source_state_id", "truth_sha256", "review_sha256", *OUTCOMES))
        elif status in MISSING:
            exact(row, fields)
        else:
            raise ValueError("unknown disposition")
        eid, arm = token(row["episode_id"]), row["arm"]
        if eid not in enrolled or arm not in ARMS or (eid,arm) in cells:
            raise ValueError("unplanned or duplicate arm")
        cells[eid,arm] = row
        if status == "reviewed":
            for k in ("model_id","source_state_id","truth_sha256"):
                if row[k] != enrolled[eid][k]:
                    raise ValueError("frozen model, source or truth mismatch")
            pin(row["review_sha256"])
            for field in OUTCOMES:
                if type(row[field]) is not bool:
                    raise ValueError("reviewed outcomes must be Boolean")
                reviewed[arm][field][eid] = row[field]
    ids = sorted(enrolled)
    status_of = lambda eid,arm: cells.get((eid,arm), {}).get("status","not_reported")
    complete = [eid for eid in ids if all(status_of(eid,a)=="reviewed" for a in ARMS)]
    counts = {arm: dict(sorted(Counter(status_of(eid,arm) for eid in ids).items())) for arm in ARMS}
    teams = {}
    for eid in ids:
        team = teams.setdefault(enrolled[eid]["team_id"], {"enrolled":0,"all_arms_reviewed":0})
        team["enrolled"] += 1
        team["all_arms_reviewed"] += int(eid in complete)
    body = {
        "format":"owg.prospective-pilot-coverage.v1",
        "plan_sha256":expected_plan_sha256,
        "observations_sha256":digest(sorted(observations,key=lambda r:(r["episode_id"],r["arm"]))),
        "enrolled":len(ids), "complete_paired_episodes":len(complete),
        "arm_dispositions":counts, "team_coverage":dict(sorted(teams.items())),
        "bounds":{a:{f:_bounds(reviewed[a][f].values(),len(ids)) for f in OUTCOMES} for a in ARMS},
        "contrasts":{right+"_vs_"+left:{f:_delta(reviewed[left][f],reviewed[right][f],ids)
                          for f in OUTCOMES} for left,right in (("control","raw"),("control","goal"),("raw","goal"))},
        "incomplete_worklist":[{"episode_id":eid,"arm":a,"status":status_of(eid,a)}
                               for eid in ids for a in ARMS if status_of(eid,a)!="reviewed"],
        "complete_coverage":len(complete)==len(ids),
        "canonical_score_eligible":len(complete)==len(ids),
        "product_effectiveness_verified":False,"provenance_verified":False,
        "limitations":["Externally pinned cohort is a declaration, not a trusted timestamp.",
                       "Unknown outcomes include withdrawn or unreported arms; no imputation.",
                       "Bounds are marginal identified ranges, not confidence intervals.",
                       "Full coverage permits a separate canonical scorer, not a product recommendation."]
    }
    return {**body,"coverage_sha256":digest(body)}


def score(plan, truth, runs, dispositions, *, expected_plan_sha256, confirmed, _observe=None):
    """Bind actual submitted truth and reviewed runs to the frozen denominator.

    The canonical existing product-value scorer is invoked only on complete
    paired coverage. No new product gate is introduced here.
    """
    from . import product_value

    if confirmed is not True:
        raise ValueError("explicit private scoring confirmation required")
    enrolled = _verify(plan, expected_plan_sha256)
    indexed_truth = _index(truth)
    if set(indexed_truth) != set(enrolled):
        raise ValueError("truth must cover the exact frozen cohort")
    for eid,row in indexed_truth.items():
        if digest(row) != enrolled[eid]["truth_sha256"]:
            raise ValueError("ground truth differs from frozen declaration")
    exact(runs, ARMS)
    if not isinstance(dispositions,list) or any(
            not isinstance(r,dict) or r.get("status") not in MISSING for r in dispositions):
        raise ValueError("only missing dispositions may be supplied separately")
    observations = list(dispositions)
    for arm in ARMS:
        for eid,run in _index(runs[arm]).items():
            if eid not in enrolled:
                raise ValueError("unplanned completed episode")
            if any(run.get(k)!=enrolled[eid][k] for k in ("model_id","source_state_id")):
                raise ValueError("run model or source differs from frozen declaration")
            reviewed = product_value._validated_review(run,episode_id=eid,arm=arm)
            observations.append({"episode_id":eid,"arm":arm,"status":"reviewed",
                                 "model_id":run["model_id"],"source_state_id":run["source_state_id"],
                                 "truth_sha256":enrolled[eid]["truth_sha256"],
                                 "review_sha256":digest(run),
                                 **{k:reviewed[k] for k in OUTCOMES}})
    coverage = inspect(plan, observations,expected_plan_sha256=expected_plan_sha256,confirmed=True)
    if _observe is not None:
        _observe(observations, coverage)
    full = coverage["complete_coverage"]
    report = product_value.report(truth, *(runs[a] for a in ARMS)) if full else None
    body = {"format":"owg.prospective-pilot-score.v1","coverage":coverage,
            "truth_sha256":digest(truth),"run_sha256":{a:digest(runs[a]) for a in ARMS},
            "canonical_report":report,"canonical_scorer_invoked":full,
            "decision_withheld_for_incomplete_coverage":not full,
            "provenance_verified":False,"product_effectiveness_verified":False}
    return {**body,"score_sha256":digest(body)}


def _load_jsonl(path):
    with path.open("rb") as f:
        data=f.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:
        raise ValueError("input too large")
    rows=[_parse(line) for line in data.splitlines() if line.strip()]
    _index(rows)
    return rows


def _parse(data):
    def unique(pairs):
        out = {}
        for k,v in pairs:
            if k in out:
                raise ValueError("duplicate JSON key")
            out[k] = v
        return out
    def invalid(_):
        raise ValueError("nonfinite JSON value")
    return json.loads(data,object_pairs_hook=unique,parse_constant=invalid)


def _load(path):
    with path.open("rb") as f:
        data = f.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:
        raise ValueError("input too large")
    return _parse(data)

def _write(path,value):
    data = canonical(value)+b"\n"
    if len(data)>MAX_BYTES:
        raise ValueError("output too large")
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"wb") as f:
        f.write(data)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    a=sub.add_parser("freeze")
    a.add_argument("--roster",type=Path,required=True)
    b=sub.add_parser("inspect")
    b.add_argument("--plan",type=Path,required=True)
    b.add_argument("--plan-sha256",required=True)
    b.add_argument("--observations",type=Path,required=True)
    s=sub.add_parser("score")
    s.add_argument("--plan",type=Path,required=True)
    s.add_argument("--plan-sha256",required=True)
    s.add_argument("--episodes",type=Path,required=True)
    s.add_argument("--dispositions",type=Path,required=True)
    for arm in ARMS:
        s.add_argument("--"+arm,type=Path,required=True)
    p=sub.add_parser("prepare")
    p.add_argument("--episodes",type=Path,required=True)
    p.add_argument("--assignments",type=Path,required=True)
    t=sub.add_parser("template")
    t.add_argument("--plan",type=Path,required=True)
    t.add_argument("--plan-sha256",required=True)
    for cmd in (a,b,s,p,t):
        cmd.add_argument("--out",type=Path,required=True)
        cmd.add_argument("--confirm",action="store_true",required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=="freeze":
            result=freeze(_load(args.roster),confirmed=args.confirm)
        elif args.command=="prepare":
            result=prepare(_load_jsonl(args.episodes),_load(args.assignments),confirmed=args.confirm)
        elif args.command=="template":
            result=disposition_template(_load(args.plan),expected_plan_sha256=args.plan_sha256,
                                        confirmed=args.confirm)
        elif args.command=="inspect":
            result=inspect(_load(args.plan),_load(args.observations),
                           expected_plan_sha256=args.plan_sha256,confirmed=args.confirm)
        else:
            result=score(_load(args.plan),_load_jsonl(args.episodes),
                         {a:_load_jsonl(getattr(args,a)) for a in ARMS},
                         _load(args.dispositions),expected_plan_sha256=args.plan_sha256,
                         confirmed=args.confirm)
        _write(args.out,result)
    except (ValueError,TypeError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Pilot cohort operation refused: invalid evidence, pin, or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
