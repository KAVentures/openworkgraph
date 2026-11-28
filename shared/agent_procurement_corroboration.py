"""Compare separately declared business evidence with retained observations.

No inference of reviewer identity or source-system truth. Pure, deterministic,
non-mutating comparison over the frozen enrolled population.
"""
from collections import Counter

FIELDS=("business_outcome","critical_incident","forbidden_action")


def _compare(declared,reviewed):
    if declared is None or reviewed is None:return "unknown"
    if declared=="unresolved":return "unknown"
    return "match" if declared==reviewed else "conflict"


def summarize(state):
    plan=state.get("monitor_plan")
    required=state.get("outcome_corroboration_required",False)
    reviews=state.get("outcome_corroborations",[])
    if not plan:
        return {"required":required,"enrolled":0,"reviewed":0,"matched":0,
                "conflicts":0,"unresolved":0,"source_overlap":0,
                "complete":False,"blocks_continuation":required,"triggers":[],"windows":[]}
    observations={o["slot_id"]:o for w in state["snapshot"]["windows"] for o in w["observations"]}
    source_refs={o["source_evidence_sha256"] for o in observations.values()}|set(state.get("initial_sources",[]))
    review_rows={x["slot_id"]:(x,b["reviewer_alias"]) for b in reviews for x in b["rows"]}
    producer_aliases={a["producer_alias"].casefold() for a in state.get("producer_attribution",{}).values()}
    totals=Counter();windows=[];triggers=[]
    for window in plan["windows"]:
        counts=Counter()
        for slot in window["plan"]["slots"]:
            sid=slot["slot_id"];counts["enrolled"]+=1
            if sid not in review_rows:
                counts["unresolved"]+=1;continue
            counts["reviewed"]+=1
            x,alias=review_rows[sid];obs=observations.get(sid)
            overlap=x["source_reference_sha256"] in source_refs or alias.casefold() in producer_aliases
            if overlap:
                counts["source_overlap"]+=1
                triggers.append({"window_id":window["window_id"],"slot_id":sid,"reason":"corroboration_source_or_alias_overlap"})
            states={}
            for field in FIELDS:
                declared=obs.get(field) if obs else None
                if field=="business_outcome" and obs and obs["status"]=="failed":
                    declared="failure"
                elif field=="business_outcome" and obs and obs["status"]!="completed":
                    declared=None
                states[field]=_compare(declared,x[field])
            conflicts=sorted(k for k,v in states.items() if v=="conflict")
            if conflicts:
                counts["conflicts"]+=1
                triggers.append({"window_id":window["window_id"],"slot_id":sid,"reason":"corroboration_disagreement","fields":conflicts})
            if x["critical_incident"] or x["forbidden_action"]:
                triggers.append({"window_id":window["window_id"],"slot_id":sid,"reason":"reviewer_declared_safety_incident"})
            if not overlap and all(v=="match" for v in states.values()):
                counts["matched"]+=1
            elif not conflicts:
                counts["unresolved"]+=1
        totals.update(counts)
        windows.append({"window_id":window["window_id"],**{k:counts[k] for k in
            ("enrolled","reviewed","matched","conflicts","unresolved","source_overlap")}})
    complete=totals["enrolled"]>0 and totals["matched"]==totals["enrolled"]
    return {"required":required,**{k:totals[k] for k in
        ("enrolled","reviewed","matched","conflicts","unresolved","source_overlap")},
        "complete":complete,"blocks_continuation":bool(triggers or required and not complete),
        "triggers":triggers,"windows":windows,
        "reviewer_identity_verified":False,"source_system_verified":False}
