"""Private counterbalanced prospective pilot schedule and append-only attempt ledger.

Offline operator declarations only. Never executes a model, reads a source or
verifies the authenticity of a session, timestamp, receipt or participant.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from itertools import permutations
from pathlib import Path

from . import prospective_pilot_cohort as cohort

ARMS = cohort.ARMS
SCHEDULE = "owg.pilot-execution-schedule.v1"
LEDGER = "owg.pilot-execution-ledger.v1"
TERMINAL = ("completed", "model_failed", "source_unavailable", "withdrawn", "other")
ORDERS = tuple(permutations(ARMS))


def _confirmed(value):
    if value is not True:
        raise ValueError("explicit private operation confirmation required")


def _rank(seed, *parts):
    return cohort.digest([seed, *parts])


def schedule(plan, *, expected_plan_sha256, seed, confirmed):
    """Deterministic blocked randomization using an independently chosen seed.

    Complete six-episode blocks use all permutations. A three-episode remainder
    uses a cyclic Latin square; one/two additional episodes preserve position
    balance. Every stratum has arm-position count imbalance at most one.
    """
    _confirmed(confirmed)
    enrolled = cohort._verify(plan, expected_plan_sha256)
    cohort.pin(seed)
    strata = defaultdict(list)
    for eid, row in enrolled.items():
        strata[row["team_id"], row["model_id"]].append(eid)
    rows = []
    summaries = []
    for (team, model), members in sorted(strata.items()):
        group = (team, model)
        members.sort(key=lambda e: (_rank(seed, "episode", *group, e), e))
        assigned = []
        for offset in range(0, len(members), 6):
            block = members[offset:offset+6]
            # Split the six permutations into two cyclic Latin squares.
            first = sorted(ARMS, key=lambda a: (_rank(seed, "latin", *group, offset, a), a))
            second = [first[0], first[2], first[1]]
            cycles = []
            for j, base in enumerate((first, second)):
                rotations = [tuple(base[i:]+base[:i]) for i in range(3)]
                rotations.sort(key=lambda order: (_rank(seed, "rotation", *group, offset, j, list(order)), order))
                cycles.extend(rotations)
            if len(block) == 6:
                cycles.sort(key=lambda order: (_rank(seed, "full-block", *group, offset, list(order)), order))
            for eid, order in zip(block, cycles):
                assigned.append(order)
                rows.append({"episode_id":eid,"arm_order":list(order),
                             "team_id":team,"model_id":model,"block":offset//6+1})
        positions = {a:[sum(order[i]==a for order in assigned) for i in range(3)] for a in ARMS}
        pairs = {a+"<"+b:sum(order.index(a)<order.index(b) for order in assigned)
                 for a in ARMS for b in ARMS if a!=b}
        summaries.append({"team_id":team,"model_id":model,"episodes":len(members),
                          "arm_position_counts":positions,"pair_precedence_counts":pairs})
    body={"format":SCHEDULE,"plan_sha256":expected_plan_sha256,"seed":seed,
          "episodes":rows,"strata":summaries,
          "protocol":"six_permutations_then_balanced_cyclic_remainder",
          "limitations":["Order is reproducible, not proof of concealed allocation or fresh sessions.",
                         "Partial blocks balance arm positions, not necessarily every pair precedence."]}
    return {**body,"schedule_sha256":cohort.digest(body)}


def verify_schedule(plan, document, *, expected_plan_sha256, expected_schedule_sha256):
    cohort.pin(expected_schedule_sha256)
    if not isinstance(document,dict) or document.get("schedule_sha256")!=expected_schedule_sha256:
        raise ValueError("external schedule pin mismatch")
    if document != schedule(plan,expected_plan_sha256=expected_plan_sha256,
                            seed=document.get("seed"),confirmed=True):
        raise ValueError("schedule differs from exact recomputation")
    return {r["episode_id"]:r for r in document["episodes"]}


def _stamp(value):
    if type(value) is not int or not 0 <= value <= 253402300799999:
        raise ValueError("declared UTC epoch milliseconds required")
    return value


def _initial(plan_pin,schedule_pin):
    return cohort.digest([LEDGER,plan_pin,schedule_pin])


def _header(plan_pin,schedule_pin,events,history):
    body={"format":LEDGER,"plan_sha256":plan_pin,"schedule_sha256":schedule_pin,
          "events":events,"revision":len(events),"history_sha256":history}
    return {**body,"ledger_sha256":cohort.digest(body)}


def initialize(plan, document, *, expected_plan_sha256, expected_schedule_sha256, confirmed):
    _confirmed(confirmed)
    verify_schedule(plan,document,expected_plan_sha256=expected_plan_sha256,
                    expected_schedule_sha256=expected_schedule_sha256)
    return _header(expected_plan_sha256,expected_schedule_sha256,[],
                   _initial(expected_plan_sha256,expected_schedule_sha256))


def _apply(states, sessions, materials, receipts, orders, enrolled, row, last_time):
    base=("episode_id","arm","action","declared_at_ms")
    if not isinstance(row,dict):
        raise ValueError("attempt action object required")
    action=row.get("action")
    if action=="start":
        cohort.exact(row,(*base,"session_id","model_id","source_state_id"))
    elif action=="completed":
        cohort.exact(row,(*base,"material_sha256","transport_sha256"))
    elif action in TERMINAL:
        cohort.exact(row,base)
    else:
        raise ValueError("unsupported attempt action")
    eid=cohort.token(row["episode_id"])
    arm=row["arm"]
    now=_stamp(row["declared_at_ms"])
    if now < last_time or eid not in orders or arm not in ARMS:
        raise ValueError("unknown attempt or regressing declared time")
    order=orders[eid]["arm_order"]
    pos=order.index(arm)
    if any(states[eid,a]["state"] not in TERMINAL for a in order[:pos]):
        raise ValueError("prior scheduled arm is not terminal")
    current=states[eid,arm]
    if current["state"] in TERMINAL:
        raise ValueError("terminal attempt cannot be retried or rewritten")
    if action=="start":
        if current["state"]!="not_started":
            raise ValueError("attempt already started")
        session=cohort.token(row["session_id"])
        if session in sessions:
            raise ValueError("fresh session identifier required")
        if any(row[k]!=enrolled[eid][k] for k in ("model_id","source_state_id")):
            raise ValueError("model/source differs from frozen plan")
        sessions.add(session)
        current.update(state="started",session_id=session,started_at_ms=now)
    elif action=="completed":
        if current["state"]!="started":
            raise ValueError("completion requires a recorded start")
        material=cohort.pin(row["material_sha256"])
        receipt=cohort.pin(row["transport_sha256"])
        if material in materials or receipt in receipts:
            raise ValueError("completion material or transport receipt reused")
        materials.add(material)
        receipts.add(receipt)
        current.update(state=action,material_sha256=material,
                       transport_sha256=receipt,terminal_at_ms=now)
    else:
        if action=="model_failed" and current["state"]!="started":
            raise ValueError("model failure requires a recorded start")
        current.update(state=action,terminal_at_ms=now)
    return now


def replay(plan, document, ledger, *, expected_plan_sha256, expected_schedule_sha256,
           expected_ledger_sha256):
    orders=verify_schedule(plan,document,expected_plan_sha256=expected_plan_sha256,
                           expected_schedule_sha256=expected_schedule_sha256)
    enrolled=cohort._verify(plan,expected_plan_sha256)
    cohort.exact(ledger,("format","plan_sha256","schedule_sha256","events","revision",
                         "history_sha256","ledger_sha256"))
    cohort.pin(expected_ledger_sha256)
    events=ledger["events"]
    if (ledger["format"]!=LEDGER or ledger["plan_sha256"]!=expected_plan_sha256
            or ledger["schedule_sha256"]!=expected_schedule_sha256
            or not isinstance(events,list) or len(events)>2*len(orders)*len(ARMS)
            or type(ledger["revision"]) is not int or ledger["revision"]!=len(events)):
        raise ValueError("invalid ledger scope or size")
    states={(eid,a):{"episode_id":eid,"arm":a,"state":"not_started"} for eid in orders for a in ARMS}
    sessions,materials,receipts=set(),set(),set()
    history=_initial(expected_plan_sha256,expected_schedule_sha256)
    last_time=0
    for i,event in enumerate(events,1):
        cohort.exact(event,("revision","previous_sha256","action","event_sha256"))
        body={"revision":i,"previous_sha256":history,"action":event["action"]}
        history=cohort.digest(body)
        if type(event["revision"]) is not int or event!={**body,"event_sha256":history}:
            raise ValueError("invalid attempt history chain")
        last_time=_apply(states,sessions,materials,receipts,orders,enrolled,event["action"],last_time)
    if (ledger!=_header(expected_plan_sha256,expected_schedule_sha256,events,history)
            or ledger["ledger_sha256"]!=expected_ledger_sha256):
        raise ValueError("external ledger pin mismatch")
    return states,orders,enrolled,sessions,materials,receipts,last_time


def append(plan, document, ledger, actions, *, expected_plan_sha256,
           expected_schedule_sha256, expected_ledger_sha256, confirmed):
    """All-or-nothing append to a new private artifact; no in-place mutation."""
    _confirmed(confirmed)
    states,orders,enrolled,sessions,materials,receipts,last_time=replay(
        plan,document,ledger,expected_plan_sha256=expected_plan_sha256,
        expected_schedule_sha256=expected_schedule_sha256,expected_ledger_sha256=expected_ledger_sha256)
    if not isinstance(actions,list) or not 1<=len(actions)<=500:
        raise ValueError("supply 1–500 new actions")
    events=list(ledger["events"])
    history=ledger["history_sha256"]
    for row in actions:
        last_time=_apply(states,sessions,materials,receipts,orders,enrolled,row,last_time)
        body={"revision":len(events)+1,"previous_sha256":history,"action":dict(row)}
        history=cohort.digest(body)
        events.append({**body,"event_sha256":history})
    return _header(expected_plan_sha256,expected_schedule_sha256,events,history)


def progress(plan, document, ledger, *, expected_plan_sha256, expected_schedule_sha256,
             expected_ledger_sha256, confirmed):
    _confirmed(confirmed)
    states,orders,*_=replay(plan,document,ledger,expected_plan_sha256=expected_plan_sha256,
                           expected_schedule_sha256=expected_schedule_sha256,
                           expected_ledger_sha256=expected_ledger_sha256)
    counts={a:dict(sorted(Counter(states[e,a]["state"] for e in orders).items())) for a in ARMS}
    cells=[]
    for eid in sorted(orders):
        for pos,a in enumerate(orders[eid]["arm_order"],1):
            row=dict(states[eid,a])
            row["position"]=pos
            row["next_action_available"]=(row["state"] not in TERMINAL and
                all(states[eid,b]["state"] in TERMINAL for b in orders[eid]["arm_order"][:pos-1]))
            cells.append(row)
    body={"format":"owg.pilot-execution-progress.v1","ledger_sha256":expected_ledger_sha256,
          "plan_sha256":expected_plan_sha256,"schedule_sha256":expected_schedule_sha256,
          "enrolled":len(orders),"arm_counts":counts,"cells":cells,
          "all_attempts_terminal":all(r["state"] in TERMINAL for r in cells),
          "all_attempts_completed":all(r["state"]=="completed" for r in cells),
          "session_freshness_verified":False,"transport_verified":False,
          "timestamps_verified":False,"product_effectiveness_verified":False}
    return {**body,"progress_sha256":cohort.digest(body)}


def _arguments(parser, *, schedule_required=True, ledger_required=False):
    parser.add_argument("--plan",type=Path,required=True)
    parser.add_argument("--plan-sha256",required=True)
    if schedule_required:
        parser.add_argument("--schedule",type=Path,required=True)
        parser.add_argument("--schedule-sha256",required=True)
    if ledger_required:
        parser.add_argument("--ledger",type=Path,required=True)
        parser.add_argument("--ledger-sha256",required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--confirm",action="store_true",required=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    s=sub.add_parser("schedule")
    _arguments(s,schedule_required=False)
    s.add_argument("--seed",required=True)
    _arguments(sub.add_parser("initialize"))
    a=sub.add_parser("append")
    _arguments(a,ledger_required=True)
    a.add_argument("--actions",type=Path,required=True)
    _arguments(sub.add_parser("progress"),ledger_required=True)
    args=parser.parse_args(argv)
    try:
        plan=cohort._load(args.plan)
        kw={"expected_plan_sha256":args.plan_sha256,"confirmed":args.confirm}
        if args.command=="schedule":
            result=schedule(plan,seed=args.seed,**kw)
        else:
            doc=cohort._load(args.schedule)
            kw["expected_schedule_sha256"]=args.schedule_sha256
            if args.command=="initialize":
                result=initialize(plan,doc,**kw)
            else:
                ledger=cohort._load(args.ledger)
                kw["expected_ledger_sha256"]=args.ledger_sha256
                result=(append(plan,doc,ledger,cohort._load(args.actions),**kw)
                        if args.command=="append" else progress(plan,doc,ledger,**kw))
        cohort._write(args.out,result)
    except (ValueError,TypeError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Pilot execution operation refused: invalid evidence, pin, or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
