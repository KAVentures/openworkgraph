"""Paired finite synthetic behavior review across immutable saved revisions.

No generated outcome is a reviewed expectation. Every input point is
evaluated on both models and dates; reports contain structural IDs and
choice indices, not supplied values. No source or execution is contacted.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import product
from threading import BoundedSemaphore
from typing import Any

from . import process_decision_domain as domain
from .process_decision_tables import evaluate_decisions, validate_date
from .process_model import ProcessModel

FORMAT="openworkgraph.differential-decision-domain.v1"
MAX_DATES=8
MAX_POINTS=4096
MAX_PAGE=50
MAX_WITNESSES=50
MAX_OBLIGATIONS=20000
SELECTED={"unique_match","first_match"}
FILTERS={"all","changed","selection_lost","selection_gained","target_changed","status_changed",
         "diagnostics_changed","identity_changed","unchanged"}
_GUARD=BoundedSemaphore(1)


def _ref(packet):
    return {k:packet[k] for k in ("workflow_id","revision","content_sha256")}


def _integer(value,low,high):
    if type(value) is not int or not low<=value<=high:
        raise ValueError("invalid bounded review paging or witness limit")
    return value


def parse(payload):
    if type(payload) is not dict or not {"dates","dimensions"}<=payload.keys() or set(payload)-{
            "dates","dimensions","filter","table_id","offset","limit","expected_review_sha256","witness_limit"}:
        raise ValueError("provide explicit dates, synthetic dimensions and optional review controls")
    dates=payload["dates"]
    if type(dates) is not list or not 1<=len(dates)<=MAX_DATES:
        raise ValueError("select one to eight distinct test dates")
    dates=[validate_date(d) for d in dates]
    if len(set(dates))!=len(dates):
        raise ValueError("test dates must be distinct")
    dates.sort()
    _,dimensions,paths,count=domain._parse({"as_of_date":dates[0],"dimensions":payload["dimensions"]})
    if count*len(dates)>MAX_POINTS:
        raise ValueError("dated finite domain exceeds 4096 points; narrow the review")
    filt=payload.get("filter","changed")
    if type(filt) is not str or filt not in FILTERS:
        raise ValueError("invalid evidence filter")
    table=payload.get("table_id")
    if table is not None and (type(table) is not str or not 1<=len(table)<=64):
        raise ValueError("invalid table identity filter")
    offset=_integer(payload.get("offset",0),0,MAX_POINTS)
    limit=_integer(payload.get("limit",25),1,MAX_PAGE)
    witnesses=_integer(payload.get("witness_limit",20),1,MAX_WITNESSES)
    expected=payload.get("expected_review_sha256")
    if expected is not None and (type(expected) is not str or len(expected)!=64 or any(c not in "0123456789abcdef" for c in expected)):
        raise ValueError("invalid exact review digest")
    if offset and expected is None:
        raise ValueError("open the first evidence page before requesting later pages")
    return dates,dimensions,paths,count,filt,table,offset,limit,witnesses,expected


def _outcome(check,table):
    if check is None:
        return {"status":"table_missing","target":None,"selected_row":None,"blocking_row":None,
                "blocking_table":None,"rows":{}}
    target=check["selected_target_activity_id"]
    selected=next((r.id for r in table.rows if r.target_activity_id==target),None) if target is not None else None
    return {"status":check["status"],"target":target,"selected_row":selected,
            "blocking_row":check.get("blocking_row_id"),"blocking_table":check.get("blocking_decision_table_id"),
            "rows":{r["row_id"]:r["status"] for r in check["rows"]}}


def _delta(ident,a,b,old_table,new_table):
    identity=old_table is None or new_table is None or old_table.decision_activity_id!=new_table.decision_activity_id
    before_selected=a["status"] in SELECTED
    after_selected=b["status"] in SELECTED
    flags=[]
    if identity:flags.append("identity_changed")
    if before_selected and not after_selected:flags.append("selection_lost")
    if after_selected and not before_selected:flags.append("selection_gained")
    if before_selected and after_selected and a["target"]!=b["target"]:flags.append("target_changed")
    if a["status"]!=b["status"]:flags.append("status_changed")
    # Keep changes in co-matching, unknown and blocked rows even where
    # the final selected target and status are identical.
    if any(a[k]!=b[k] for k in ("selected_row","blocking_row","blocking_table","rows")):
        flags.append("diagnostics_changed")
    changed_rows=[{"row_id":k,"before":a["rows"].get(k,"not_evaluated_or_absent"),
                   "after":b["rows"].get(k,"not_evaluated_or_absent")}
                  for k in sorted(a["rows"].keys()|b["rows"].keys()) if a["rows"].get(k)!=b["rows"].get(k)]
    primary=next((f for f in ("identity_changed","selection_lost","selection_gained","target_changed",
                              "status_changed","diagnostics_changed") if f in flags),"unchanged")
    return {"table_id":ident,"before_status":a["status"],"after_status":b["status"],
            "before_target":a["target"],"after_target":b["target"],
            "before_selected_row":a["selected_row"],"after_selected_row":b["selected_row"],
            "before_blocking_row":a["blocking_row"],"after_blocking_row":b["blocking_row"],
            "before_blocking_table":a["blocking_table"],"after_blocking_table":b["blocking_table"],
            "classification":primary,"flags":flags,"changed_rows":changed_rows}


def _obligations(row,date):
    """Cover distinct changed structural signatures, per date and table.

    A row diagnostic transition is a separate obligation from a selected
    target/status transition. Dates are distinct to retain lifecycle changes.
    """
    if not row["flags"]:return set()
    key=(date,row["table_id"])
    obligations={(*key,"outcome",row["classification"],row["before_status"],row["after_status"],
                  row["before_target"],row["after_target"],row["before_selected_row"],row["after_selected_row"],
                  row["before_blocking_row"],row["after_blocking_row"],row["before_blocking_table"],row["after_blocking_table"])}
    obligations.update((*key,"row",r["row_id"],r["before"],r["after"]) for r in row["changed_rows"])
    return obligations


def compact_witnesses(points,limit):
    """Deterministic greedy set cover; no claim of cardinality optimality."""
    universe=set().union(*(p["obligations"] for p in points)) if points else set()
    if len(universe)>MAX_OBLIGATIONS:
        raise ValueError("changed witness obligation budget exceeded; narrow the domain")
    uncovered=set(universe)
    remaining=list(points)
    chosen=[]
    while uncovered and len(chosen)<limit:
        best=max(remaining,key=lambda p:len(p["obligations"]&uncovered))
        new=best["obligations"]&uncovered
        if not new:break
        uncovered-=new
        chosen.append({"point_index":best["point_index"],"as_of_date":best["as_of_date"],
                       "choice_indices":best["choice_indices"],"new_obligations_covered":len(new),
                       "changed_table_ids":best["changed_table_ids"]})
        remaining.remove(best)
    largest=max((len(p["obligations"]) for p in points),default=0)
    lower=(len(universe)+largest-1)//largest if largest else 0
    return {"selected":chosen,"selected_points":len(chosen),"distinct_obligations":len(universe),
            "covered_obligations":len(universe)-len(uncovered),"uncovered_obligations":len(uncovered),
            "all_obligations_covered":not uncovered,"minimum_possible_point_count_lower_bound":lower,
            "optimal_point_count_proven":not uncovered and len(chosen)==lower,
            "algorithm":"greedy_max_new_obligations_then_original_point_order",
            "expectations_reviewed":False,"representative_business_cases_verified":False}


def compare(before,after,payload):
    dates,dimensions,paths,count,filt,focus,offset,limit,witness_limit,expected=parse(payload)
    if before["workflow_id"]!=after["workflow_id"] or before["revision"]>=after["revision"]:
        raise ValueError("select two ordered revisions of one saved process")
    a,b=(ProcessModel.model_validate(p["process_model"]) for p in (before,after))
    old={t.id:t for t in a.decision_tables};new={t.id:t for t in b.decision_tables}
    ids=sorted(old.keys()|new.keys())
    if not ids:raise ValueError("neither revision contains a decision table")
    if focus is not None and focus not in ids:raise ValueError("table is absent from both revisions")
    basis={"format":FORMAT,"before":_ref(before),"after":_ref(after),"dates":dates,"dimensions":dimensions}
    pin=domain._digest(basis)
    if expected is not None and expected!=pin:raise ValueError("review inputs changed; prepare the exact domain again")
    if not _GUARD.acquire(blocking=False):raise ValueError("another local differential decision review is running")
    try:
        table_stats={k:{"primary":Counter(),"flags":Counter(),"status_pairs":Counter(),
                        "target_pairs":Counter(),"row_transitions":Counter()} for k in ids}
        date_stats={d:Counter() for d in dates}
        primary=Counter();flag_counts=Counter()
        points=[];page=[];matching=0;changed_points=0;point_index=0
        for date in dates:
            for indices in product(*(range(len(d["choices"])) for d in dimensions)):
                facts=domain._facts(dimensions,paths,indices)
                left={x["table_id"]:x for x in evaluate_decisions(a,facts,date)}
                right={x["table_id"]:x for x in evaluate_decisions(b,facts,date)}
                rows=[];obligations=set()
                for ident in ids:
                    x=_outcome(left.get(ident),old.get(ident))
                    y=_outcome(right.get(ident),new.get(ident))
                    row=_delta(ident,x,y,old.get(ident),new.get(ident))
                    rows.append(row);acc=table_stats[ident]
                    primary[row["classification"]]+=1;acc["primary"][row["classification"]]+=1
                    acc["flags"].update(row["flags"]);flag_counts.update(row["flags"])
                    acc["status_pairs"][(x["status"],y["status"])]+=1
                    acc["target_pairs"][(x["target"],y["target"])]+=1
                    for r in row["changed_rows"]:
                        acc["row_transitions"][(r["row_id"],r["before"],r["after"])]+=1
                    obligations.update(_obligations(row,date))
                changes=[r["table_id"] for r in rows if r["flags"]]
                if changes:
                    changed_points+=1
                    points.append({"point_index":point_index,"as_of_date":date,"choice_indices":list(indices),
                                   "changed_table_ids":changes,"obligations":obligations})
                date_stats[date]["points"]+=1
                date_stats[date]["changed_points"]+=bool(changes)
                date_stats[date]["changed_table_points"]+=len(changes)
                def match(r):
                    if focus is not None and r["table_id"]!=focus:return False
                    if filt=="all":return True
                    if filt=="changed":return bool(r["flags"])
                    if filt=="unchanged":return not r["flags"]
                    return filt in r["flags"]
                matched=[r for r in rows if match(r)]
                if matched:
                    if offset<=matching<offset+limit:
                        page.append({"point_index":point_index,"as_of_date":date,"choice_indices":list(indices),
                                     "matching_tables":matched,"all_changed_table_ids":changes})
                    matching+=1
                point_index+=1
        tables=[]
        for ident,acc in table_stats.items():
            tables.append({"table_id":ident,"present_before":ident in old,"present_after":ident in new,
                "primary_counts":dict(sorted(acc["primary"].items())),"flag_counts":dict(sorted(acc["flags"].items())),
                "status_transitions":[{"before":a,"after":b,"count":n} for (a,b),n in sorted(acc["status_pairs"].items())],
                "target_transitions":[{"before":a,"after":b,"count":n} for (a,b),n in sorted(acc["target_pairs"].items(),key=lambda x:str(x[0]))],
                "row_diagnostic_transitions":[{"row_id":r,"before":a,"after":b,"count":n}
                    for (r,a,b),n in sorted(acc["row_transitions"].items())]})
        return {"format":FORMAT,"before":_ref(before),"after":_ref(after),"review_sha256":pin,
                "dates":dates,"dimensions":[{"path":d["path"],"choice_count":len(d["choices"])} for d in dimensions],
                "combinations_per_date":count,"dated_points":point_index,"table_points":point_index*len(ids),
                "changed_points":changed_points,"primary_counts":dict(sorted(primary.items())),
                "nonexclusive_flag_counts":dict(sorted(flag_counts.items())),
                "per_date":[{"as_of_date":d,**date_stats[d]} for d in dates],"tables":tables,
                "witness_suite":compact_witnesses(points,witness_limit),
                "evidence_page":{"filter":filt,"table_id":focus,"offset":offset,"limit":limit,"matching_points":matching,
                                 "next_offset":offset+len(page) if page and offset+len(page)<matching else None,"rows":page},
                "complete_declared_finite_domain":True,"arbitrary_input_domain_equivalence_proven":False,
                "human_expectations_reviewed":False,"input_values_retained":False,
                "policy_compliance_verified":False,"execution_authorized":False,
                "interpretation":"Paired behavior over the exact finite synthetic domain and declared dates only. Selection loss or gain is not a regression or improvement without independently reviewed expectations. "
                    "Changes in row diagnostics may exist without changed selection. Witnesses cover declared structural transition signatures, not all changed combinations or policy correctness. "
                    "No data distribution, business outcome, approval or execution permission is inferred."}
    finally:_GUARD.release()
