"""Single-device private pilot workspace; no models, network or background jobs.

The database owns one accepted head under SQLite transactions. External pins
are still needed to detect replacement/rollback of the entire database.
Raw prompts, response text, review sheets and transport receipts are never
stored in the workspace; review artifacts remain separately controlled files.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from . import prospective_pilot_cohort as cohort
from . import pilot_execution as execution
from . import pilot_blinded_batches as blinded

FORMAT="owg.local-pilot-workspace.v1"
MAX_REVISIONS=5000
MAX_BATCH_RECORDS=250


class Conflict(ValueError):
    """The accepted local head changed or an active assignment conflicts."""


def _head(body):
    return {**body,"head_sha256":cohort.digest(body)}


def _body(state):
    return {k:v for k,v in state.items() if k!="head_sha256"}


def _pins(state):
    return {"expected_plan_sha256":state["plan"]["plan_sha256"],
            "expected_schedule_sha256":state["schedule"]["schedule_sha256"],
            "expected_ledger_sha256":state["ledger"]["ledger_sha256"],"confirmed":True}


def _initial(plan,schedule,plan_pin,schedule_pin):
    ledger=execution.initialize(plan,schedule,expected_plan_sha256=plan_pin,
                               expected_schedule_sha256=schedule_pin,confirmed=True)
    return _head({"format":FORMAT,"revision":0,"previous_head_sha256":None,
                  "plan":plan,"schedule":schedule,"ledger":ledger,
                  "batches":[],"withdrawn_episode_ids":[]})


def _state_cells(state):
    pins=_pins(state);pins.pop("confirmed")
    cells,_,enrolled,*_=execution.replay(state["plan"],state["schedule"],state["ledger"],**pins)
    return cells,enrolled


def _validate(state):
    cohort.exact(state,("format","revision","previous_head_sha256","plan","schedule",
                       "ledger","batches","withdrawn_episode_ids","head_sha256"))
    if (state["format"]!=FORMAT or type(state["revision"]) is not int or
            not 0<=state["revision"]<=MAX_REVISIONS or
            cohort.pin(state["head_sha256"])!=cohort.digest(_body(state))):
        raise ValueError("invalid workspace state digest")
    if state["revision"]==0:
        if state["previous_head_sha256"] is not None:
            raise ValueError("invalid initial head")
    else:
        cohort.pin(state["previous_head_sha256"])
    cells,enrolled=_state_cells(state)
    withdrawn=state["withdrawn_episode_ids"]
    if (not isinstance(withdrawn,list) or withdrawn!=sorted(set(withdrawn)) or
            any(e not in enrolled for e in withdrawn)):
        raise ValueError("invalid withdrawn roster")
    if any(cells[e,a]["state"] not in execution.TERMINAL for e in withdrawn for a in cohort.ARMS):
        raise ValueError("withdrawn episode has nonterminal attempt")
    rows=state["batches"]
    if not isinstance(rows,list) or len(rows)>MAX_BATCH_RECORDS:
        raise ValueError("invalid batch inventory")
    active_cells=set();all_pins=set();all_ids=set();active=0
    events=state["ledger"]["events"]
    completed={(e["action"]["episode_id"],e["action"]["arm"]):e["revision"]
               for e in events if e["action"]["action"]=="completed"}
    for row in rows:
        cohort.exact(row,("batch_sha256","packet_sha256","ledger_sha256","ledger_revision",
                          "assignments","status","retirement_reason"))
        batch_pin=cohort.pin(row["batch_sha256"])
        cohort.pin(row["packet_sha256"])
        if batch_pin in all_pins:
            raise ValueError("duplicate batch inventory pin")
        all_pins.add(batch_pin)
        revision=row["ledger_revision"]
        if type(revision) is not int or not 1<=revision<=len(events):
            raise ValueError("invalid batch ledger revision")
        old=execution._header(state["plan"]["plan_sha256"],state["schedule"]["schedule_sha256"],
                              events[:revision],events[revision-1]["event_sha256"])
        if old["ledger_sha256"]!=row["ledger_sha256"]:
            raise ValueError("batch references a different accepted history")
        is_active=row["status"]=="active"
        if (row["status"] not in ("active","retired") or
                (is_active and row["retirement_reason"] is not None) or
                (not is_active and row["retirement_reason"] not in ("reissue","withdrawal"))):
            raise ValueError("invalid inventory status")
        active+=int(is_active)
        assignments=row["assignments"]
        if not isinstance(assignments,list) or not 1<=len(assignments)<=len(cells):
            raise ValueError("invalid batch assignment list")
        seen=set()
        for item in assignments:
            cohort.exact(item,("review_id","episode_id","arm","material_sha256"))
            rid=cohort.token(item["review_id"]);cell=(cohort.token(item["episode_id"]),item["arm"])
            if rid in all_ids or cell in seen or cell not in completed or completed[cell]>revision:
                raise ValueError("duplicate or uncompleted assignment")
            if item["material_sha256"]!=cells[cell]["material_sha256"]:
                raise ValueError("assignment material pin mismatch")
            all_ids.add(rid);seen.add(cell)
            if is_active:
                if cell in active_cells or cell[0] in withdrawn:
                    raise ValueError("conflicting active assignment")
                active_cells.add(cell)
    if active>blinded.MAX_BATCHES:
        raise ValueError("active batch limit exceeded")
    return cells


def _schema(conn):
    conn.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
    conn.execute("""CREATE TABLE history (revision INTEGER PRIMARY KEY, previous_head TEXT,
                    head TEXT NOT NULL UNIQUE, operation TEXT NOT NULL)""")


def _connect(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("existing regular workspace file required")
    # URI rw mode prevents silently creating an empty replacement database.
    conn=sqlite3.connect(path.resolve().as_uri()+"?mode=rw",uri=True,timeout=10,isolation_level=None)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def _transaction(path, *, write=False):
    conn=_connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _load(conn):
    row=conn.execute("SELECT body FROM workspace WHERE id=1").fetchone()
    if row is None or len(row[0].encode("utf-8"))>cohort.MAX_BYTES:
        raise ValueError("missing or oversized workspace")
    state=json.loads(row[0]);_validate(state)
    history=conn.execute("SELECT revision,previous_head,head,operation FROM history ORDER BY revision LIMIT ?",
                         (MAX_REVISIONS+2,)).fetchall()
    if len(history)!=state["revision"]+1:
        raise ValueError("workspace history length mismatch")
    prior=None
    for i,(revision,prev,head,operation) in enumerate(history):
        if revision!=i or prev!=prior or operation not in (
                "initialize","append","prepare_batch","retire_batch","withdraw_episode"):
            raise ValueError("invalid workspace history chain")
        if (i==0)!=(operation=="initialize"):
            raise ValueError("invalid initialization record")
        prior=cohort.pin(head)
    if prior!=state["head_sha256"] or history[-1][1]!=state["previous_head_sha256"]:
        raise ValueError("workspace history head mismatch")
    return state


def _match(state,expected):
    if cohort.pin(expected)!=state["head_sha256"]:
        raise Conflict("workspace head changed; inspect latest state before retrying")


def _store(conn,state):
    encoded=cohort.canonical(state)
    if len(encoded)>cohort.MAX_BYTES:
        raise ValueError("workspace capacity exceeded")
    conn.execute("UPDATE workspace SET body=? WHERE id=1",(encoded.decode("utf-8"),))


def _commit(conn,state,body,operation):
    next_body={**body,"revision":state["revision"]+1,"previous_head_sha256":state["head_sha256"]}
    next_state=_head(next_body);_validate(next_state)
    _store(conn,next_state)
    conn.execute("INSERT INTO history VALUES (?,?,?,?)",(next_state["revision"],
                 state["head_sha256"],next_state["head_sha256"],operation))
    return next_state


def initialize(path,plan,schedule,*,expected_plan_sha256,expected_schedule_sha256,confirmed):
    execution._confirmed(confirmed)
    state=_initial(plan,schedule,expected_plan_sha256,expected_schedule_sha256)
    _validate(state)
    # Exclusive initial file. The containing directory must be private.
    path=Path(path);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    try:
        with _transaction(path,write=True) as conn:
            _schema(conn)
            conn.execute("INSERT INTO workspace VALUES (1,?)",(cohort.canonical(state).decode("utf-8"),))
            conn.execute("INSERT INTO history VALUES (0,NULL,?,?)",(state["head_sha256"],"initialize"))
    except BaseException:
        # Only the file we just exclusively created is removed.
        path.unlink(missing_ok=True)
        raise
    return state


def status(path,*,expected_head_sha256=None):
    with _transaction(path) as conn:
        state=_load(conn)
        if expected_head_sha256 is not None:
            _match(state,expected_head_sha256)
        return state


def history(path,*,expected_head_sha256):
    with _transaction(path) as conn:
        state=_load(conn);_match(state,expected_head_sha256)
        rows=[{"revision":r,"previous_head_sha256":p,"head_sha256":h,"operation":o}
              for r,p,h,o in conn.execute("SELECT revision,previous_head,head,operation FROM history ORDER BY revision")]
        return {"head_sha256":state["head_sha256"],"history":rows,"historical_state_bodies_retained":False}


def append(path,actions,*,expected_head_sha256,confirmed):
    execution._confirmed(confirmed)
    with _transaction(path,write=True) as conn:
        state=_load(conn);_match(state,expected_head_sha256)
        ledger=execution.append(state["plan"],state["schedule"],state["ledger"],actions,**_pins(state))
        return _commit(conn,state,{**_body(state),"ledger":ledger},"append")


def _inventory(key):
    return {"batch_sha256":key["batch_sha256"],"packet_sha256":key["packet_sha256"],
            "ledger_sha256":key["ledger_sha256"],"ledger_revision":key["ledger_revision"],
            "assignments":[{"review_id":rid,**m} for rid,m in sorted(key["mapping"].items())],
            "status":"active","retirement_reason":None}


def prepare_batch(path,truth,materials,*,expected_head_sha256,packet_path,key_path,confirmed):
    execution._confirmed(confirmed)
    packet_path=Path(packet_path);key_path=Path(key_path)
    if packet_path.resolve()==key_path.resolve() or packet_path.exists() or key_path.exists():
        raise ValueError("distinct new packet and private key paths required")
    with _transaction(path,write=True) as conn:
        state=_load(conn);_match(state,expected_head_sha256)
        packet,key=blinded.prepare(state["plan"],state["schedule"],state["ledger"],truth,materials,**_pins(state))
        row=_inventory(key)
        next_body={**_body(state),"batches":[*state["batches"],row]}
        # Validate before material is written; never allocate overlapping cells.
        candidate=_head({**next_body,"revision":state["revision"]+1,"previous_head_sha256":state["head_sha256"]})
        _validate(candidate)
        # Exclusive writes precede commit. If a later write/commit fails,
        # any resulting files are unregistered and must not be distributed.
        cohort._write(key_path,key)
        cohort._write(packet_path,packet)
        return _commit(conn,state,next_body,"prepare_batch")


def retire_batch(path,batch_sha256,*,expected_head_sha256,confirmed):
    execution._confirmed(confirmed);cohort.pin(batch_sha256)
    with _transaction(path,write=True) as conn:
        state=_load(conn);_match(state,expected_head_sha256)
        selected=[r for r in state["batches"] if r["batch_sha256"]==batch_sha256 and r["status"]=="active"]
        if len(selected)!=1:
            raise ValueError("exact active batch required")
        rows=[{**r,"status":"retired","retirement_reason":"reissue"} if r is selected[0] else r
              for r in state["batches"]]
        return _commit(conn,state,{**_body(state),"batches":rows},"retire_batch")


def withdraw_episode(path,episode_id,declared_at_ms,*,expected_head_sha256,confirmed):
    execution._confirmed(confirmed);eid=cohort.token(episode_id);execution._stamp(declared_at_ms)
    with _transaction(path,write=True) as conn:
        state=_load(conn);_match(state,expected_head_sha256)
        cells=_validate(state)
        if (eid,cohort.ARMS[0]) not in cells or eid in state["withdrawn_episode_ids"]:
            raise ValueError("unknown or already withdrawn episode")
        ledger=state["ledger"]
        if ledger["events"] and declared_at_ms<ledger["events"][-1]["action"]["declared_at_ms"]:
            raise ValueError("withdrawal time precedes accepted history")
        if any(cells[eid,a]["state"] not in execution.TERMINAL for a in cohort.ARMS):
            ledger=execution.withdraw_remaining(state["plan"],state["schedule"],ledger,
                episode_id=eid,declared_at_ms=declared_at_ms,**_pins(state))
        rows=[{**r,"status":"retired","retirement_reason":"withdrawal"}
              if r["status"]=="active" and any(x["episode_id"]==eid for x in r["assignments"]) else r
              for r in state["batches"]]
        return _commit(conn,state,{**_body(state),"ledger":ledger,"batches":rows,
            "withdrawn_episode_ids":sorted([*state["withdrawn_episode_ids"],eid])},"withdraw_episode")


def progress(path,*,expected_head_sha256):
    state=status(path,expected_head_sha256=expected_head_sha256)
    result=execution.progress(state["plan"],state["schedule"],state["ledger"],**_pins(state))
    return {"head_sha256":state["head_sha256"],"revision":state["revision"],"execution":result,
            "batch_inventory":state["batches"],"withdrawn_episode_ids":state["withdrawn_episode_ids"]}


def coverage(path,*,expected_head_sha256):
    state=status(path,expected_head_sha256=expected_head_sha256)
    cells=_validate(state)
    assigned={(x["episode_id"],x["arm"]) for r in state["batches"] if r["status"]=="active" for x in r["assignments"]}
    observations=[]
    for (eid,arm),row in sorted(cells.items()):
        raw=row["state"]
        disposition=("withdrawn" if eid in state["withdrawn_episode_ids"] else
            ("review_pending" if (eid,arm) in assigned else "review_unavailable") if raw=="completed" else
            "other" if raw=="started" else raw)
        observations.append({"episode_id":eid,"arm":arm,"status":disposition})
    result=cohort.inspect(state["plan"],observations,expected_plan_sha256=state["plan"]["plan_sha256"],confirmed=True)
    return {"head_sha256":state["head_sha256"],"coverage":result,"review_material_used":False}


def reconcile(path,truth,batches,*,expected_head_sha256,confirmed):
    execution._confirmed(confirmed)
    state=status(path,expected_head_sha256=expected_head_sha256)
    cells=_validate(state)
    if not isinstance(batches,list) or len(batches)>blinded.MAX_BATCHES:
        raise ValueError("invalid submitted batch collection")
    active={r["batch_sha256"]:r for r in state["batches"] if r["status"]=="active"}
    submitted=set()
    for item in batches:
        if not isinstance(item,dict) or not isinstance(item.get("key"),dict):
            raise ValueError("submitted batch must contain its private key")
        pin=cohort.pin(item.get("expected_batch_sha256"))
        if pin not in active or pin in submitted or _inventory(item["key"])!=active[pin]:
            raise Conflict("unregistered, retired, changed or repeated batch")
        submitted.add(pin)
    dispositions=[]
    for (eid,arm),row in sorted(cells.items()):
        if row["state"]=="completed" and eid in state["withdrawn_episode_ids"]:
            dispositions.append({"episode_id":eid,"arm":arm,"status":"withdrawn"})
    for pin,row in active.items():
        if pin not in submitted:
            dispositions.extend({"episode_id":x["episode_id"],"arm":x["arm"],"status":"review_pending"}
                                for x in row["assignments"])
    result=blinded.reconcile(state["plan"],state["schedule"],state["ledger"],truth,batches,
                             review_dispositions=dispositions,**_pins(state))
    body={"format":"owg.local-pilot-reconciliation.v1","head_sha256":state["head_sha256"],
          "revision":state["revision"],"active_batches":len(active),"submitted_batches":len(submitted),
          "result":result,"current_head_at_finish_not_verified":True}
    return {**body,"report_sha256":cohort.digest(body)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    i=sub.add_parser("initialize")
    for name in ("plan","schedule"):
        i.add_argument("--"+name,type=Path,required=True)
        i.add_argument("--"+name+"-sha256",required=True)
    a=sub.add_parser("append");a.add_argument("--actions",type=Path,required=True)
    b=sub.add_parser("prepare-batch")
    for name in ("episodes","materials","packet","private-key"):
        b.add_argument("--"+name,type=Path,required=True)
    t=sub.add_parser("retire-batch");t.add_argument("--batch-sha256",required=True)
    w=sub.add_parser("withdraw-episode")
    w.add_argument("--episode-id",required=True);w.add_argument("--declared-at-ms",type=int,required=True)
    r=sub.add_parser("reconcile")
    r.add_argument("--episodes",type=Path,required=True);r.add_argument("--batches",type=Path,required=True)
    commands=(i,a,b,t,w,r,*[sub.add_parser(name) for name in ("status","history","progress","coverage")])
    for cmd in commands:
        cmd.add_argument("--db",type=Path,required=True)
        cmd.add_argument("--out",type=Path,required=True)
        cmd.add_argument("--confirm",action="store_true",required=True)
        if cmd is not i:
            cmd.add_argument("--head-sha256",required=cmd.prog.split()[-1]!="status")
    args=parser.parse_args(argv)
    try:
        if args.out.exists():
            raise ValueError("choose a new output file")
        if args.command=="initialize":
            result=initialize(args.db,cohort._load(args.plan),cohort._load(args.schedule),
                expected_plan_sha256=args.plan_sha256,expected_schedule_sha256=args.schedule_sha256,confirmed=args.confirm)
        else:
            kw={"expected_head_sha256":args.head_sha256}
            if args.command=="append":
                result=append(args.db,cohort._load(args.actions),confirmed=args.confirm,**kw)
            elif args.command=="prepare-batch":
                result=prepare_batch(args.db,cohort._load_jsonl(args.episodes),cohort._load(args.materials),
                        packet_path=args.packet,key_path=args.private_key,confirmed=args.confirm,**kw)
            elif args.command=="retire-batch":
                result=retire_batch(args.db,args.batch_sha256,confirmed=args.confirm,**kw)
            elif args.command=="withdraw-episode":
                result=withdraw_episode(args.db,args.episode_id,args.declared_at_ms,confirmed=args.confirm,**kw)
            elif args.command=="reconcile":
                result=reconcile(args.db,cohort._load_jsonl(args.episodes),cohort._load(args.batches),confirmed=args.confirm,**kw)
            else:
                result={"status":status,"history":history,"progress":progress,"coverage":coverage}[args.command](args.db,**kw)
        cohort._write(args.out,result)
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error,RecursionError,OverflowError):
        parser.exit(2,"Workspace operation refused or output unavailable; inspect accepted head before retrying.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
