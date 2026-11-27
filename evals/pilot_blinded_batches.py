"""Partial blinded review batches over a frozen prospective execution ledger.

Reviewer packets contain operator-approved text only. The private key binds
each opaque review ID to an exact completed attempt. No external actions.
"""
from __future__ import annotations

import argparse
import secrets
from random import SystemRandom
from pathlib import Path

from . import prospective_pilot_cohort as cohort
from . import pilot_execution as execution
from . import blinded_product_review as blinded
from . import multi_reviewer_product_review as multi
from . import product_value

FORMAT="owg.pilot-blinded-batch.v1"
MAX_BATCHES=50
MATERIAL_FIELDS=("episode_id","arm","session_id","model_id","source_state_id",
                 "transport_sha256","review_text","review_material_approved")


def _truth(plan, truth, plan_pin):
    enrolled=cohort._verify(plan,plan_pin)
    indexed=cohort._index(truth)
    if set(indexed)!=set(enrolled) or any(
            cohort.digest(indexed[eid])!=enrolled[eid]["truth_sha256"] for eid in indexed):
        raise ValueError("ground truth differs from frozen cohort")
    return indexed


def _text(value):
    if not isinstance(value,str) or not value.strip() or len(value.encode("utf-8"))>65536:
        raise ValueError("bounded approved review material required")
    return value


def _material(state, enrollment, review_text):
    return {"episode_id":state["episode_id"],"arm":state["arm"],
            "session_id":state["session_id"],"model_id":enrollment["model_id"],
            "source_state_id":enrollment["source_state_id"],
            "transport_sha256":state["transport_sha256"],
            "review_text":_text(review_text),"review_material_approved":True}


def prepare(plan, schedule, ledger, truth, materials, *, expected_plan_sha256,
            expected_schedule_sha256, expected_ledger_sha256, confirmed, id_factory=None):
    execution._confirmed(confirmed)
    states,_,enrolled,*_=execution.replay(plan,schedule,ledger,
        expected_plan_sha256=expected_plan_sha256,expected_schedule_sha256=expected_schedule_sha256,
        expected_ledger_sha256=expected_ledger_sha256)
    indexed=_truth(plan,truth,expected_plan_sha256)
    if not isinstance(materials,list) or not 1<=len(materials)<=len(states):
        raise ValueError("select at least one completed attempt for blinded review")
    token=id_factory or (lambda:secrets.token_hex(16))
    seen=set();entries=[];mapping={}
    for material in materials:
        cohort.exact(material,MATERIAL_FIELDS)
        eid=cohort.token(material["episode_id"]);arm=material["arm"]
        if arm not in cohort.ARMS or eid not in enrolled or (eid,arm) in seen:
            raise ValueError("unknown or duplicate attempt material")
        seen.add((eid,arm));state=states[eid,arm]
        if state["state"]!="completed" or material["review_material_approved"] is not True:
            raise ValueError("exact completed and approved attempt required")
        expected=_material(state,enrolled[eid],material["review_text"])
        if material!=expected or cohort.digest(material)!=state["material_sha256"]:
            raise ValueError("material differs from completed attempt")
        rid=cohort.token(token())
        if rid in mapping:
            raise ValueError("duplicate generated review ID")
        entries.append({"review_id":rid,"prompt":_text(indexed[eid].get("prompt")),
                        "review_text":material["review_text"]})
        mapping[rid]={"episode_id":eid,"arm":arm,"material_sha256":state["material_sha256"]}
    SystemRandom().shuffle(entries)
    packet={"format":FORMAT,"entries":entries,
            "limitations":"Operator-approved free text; blinding is not guaranteed."}
    body={"format":FORMAT,"plan_sha256":expected_plan_sha256,
          "schedule_sha256":expected_schedule_sha256,"ledger_sha256":expected_ledger_sha256,
          "ledger_revision":ledger["revision"],"packet_sha256":cohort.digest(packet),"mapping":mapping}
    key={**body,"batch_sha256":cohort.digest(body)}
    if len(cohort.canonical(packet))>cohort.MAX_BYTES or len(cohort.canonical(key))>cohort.MAX_BYTES:
        raise ValueError("batch too large; split approved material into separate batches")
    return packet,key


def template(packet):
    cohort.exact(packet,("format","entries","limitations"))
    if packet["format"]!=FORMAT or packet["limitations"]!="Operator-approved free text; blinding is not guaranteed.":
        raise ValueError("unsupported blinded packet")
    entries=packet["entries"]
    if not isinstance(entries,list) or not 1<=len(entries)<=cohort.MAX_EPISODES*len(cohort.ARMS):
        raise ValueError("invalid review packet size")
    seen=set()
    for entry in entries:
        cohort.exact(entry,("review_id","prompt","review_text"))
        rid=cohort.token(entry["review_id"])
        if rid in seen:
            raise ValueError("duplicate review ID")
        seen.add(rid);_text(entry["prompt"]);_text(entry["review_text"])
    return blinded.review_template(packet)


def _batch(plan,schedule,ledger,states,enrolled,truth,batch,completion_revisions):
    cohort.exact(batch,("packet","key","expected_batch_sha256","reviews"))
    packet,key=batch["packet"],batch["key"]
    cohort.exact(key,("format","plan_sha256","schedule_sha256","ledger_sha256",
                      "ledger_revision","packet_sha256","mapping","batch_sha256"))
    cohort.exact(packet,("format","entries","limitations"))
    revision=key["ledger_revision"]
    if (key["format"]!=FORMAT or packet["format"]!=FORMAT or
            packet["limitations"]!="Operator-approved free text; blinding is not guaranteed." or
            key["plan_sha256"]!=ledger["plan_sha256"] or key["schedule_sha256"]!=ledger["schedule_sha256"] or
            type(revision) is not int or not 1<=revision<=ledger["revision"] or
            key["packet_sha256"]!=cohort.digest(packet) or
            key["batch_sha256"]!=cohort.pin(batch["expected_batch_sha256"]) or
            key["batch_sha256"]!=cohort.digest({k:v for k,v in key.items() if k!="batch_sha256"})):
        raise ValueError("batch differs from exact independent pins")
    prefix=ledger["events"][:revision]
    old=execution._header(ledger["plan_sha256"],ledger["schedule_sha256"],
                          prefix,prefix[-1]["event_sha256"])
    if old["ledger_sha256"]!=key["ledger_sha256"]:
        raise ValueError("batch ledger is not an exact prefix of current history")
    entries=packet["entries"];mapping=key["mapping"]
    if not isinstance(entries,list) or not isinstance(mapping,dict) or not 1<=len(entries)<=len(states):
        raise ValueError("invalid bounded review batch")
    found={};cells=set()
    for entry in entries:
        cohort.exact(entry,("review_id","prompt","review_text"))
        rid=cohort.token(entry["review_id"])
        if rid in found or rid not in mapping:
            raise ValueError("duplicate or unknown public review ID")
        meta=mapping[rid]
        cohort.exact(meta,("episode_id","arm","material_sha256"))
        eid=cohort.token(meta["episode_id"]);arm=meta["arm"]
        if eid not in enrolled or arm not in cohort.ARMS or (eid,arm) in cells:
            raise ValueError("invalid or duplicate attempt binding")
        state=states[eid,arm]
        if (state["state"]!="completed" or completion_revisions[eid,arm]>revision or
                meta["material_sha256"]!=state["material_sha256"] or
                entry["prompt"]!=_text(truth[eid].get("prompt")) or
                cohort.digest(_material(state,enrolled[eid],entry["review_text"]))!=state["material_sha256"]):
            raise ValueError("blinded entry does not match the recorded completed attempt")
        cells.add((eid,arm));found[rid]=meta
    if set(found)!=set(mapping):
        raise ValueError("private mapping coverage mismatch")
    reviews=batch["reviews"]
    if not isinstance(reviews,list) or len(reviews)>len(found):
        raise ValueError("invalid review sheet")
    submitted=set()
    for row in reviews:
        if not isinstance(row,dict):
            raise ValueError("review row object required")
        rid=cohort.token(row.get("review_id"))
        if rid not in found or rid in submitted:
            raise ValueError("unknown or duplicate submitted review")
        submitted.add(rid)
    forbidden={multi._identity(t["ground_truth_verified_by"])
               for t in truth.values() if t.get("ground_truth_verified_by")}
    indexed,_=multi._sheet(reviews,submitted,forbidden)
    out={}
    for rid,row in indexed.items():
        meta=found[rid];eid,arm=meta["episode_id"],meta["arm"]
        state=states[eid,arm]
        out[eid,arm]={"episode_id":eid,"recorded_model_completion":True,
                      "model_id":enrolled[eid]["model_id"],"source_state_id":enrolled[eid]["source_state_id"],
                      "session_id":state["session_id"],"transport_sha256":state["transport_sha256"],
                      "material_sha256":state["material_sha256"],"batch_sha256":key["batch_sha256"],
                      "independent_review":{k:row[k] for k in ("reviewer_id",*product_value.REVIEW_FIELDS)}}
    return cells,set(found),out


def reconcile(plan,schedule,ledger,truth,batches,*,expected_plan_sha256,
              expected_schedule_sha256,expected_ledger_sha256,confirmed,review_dispositions=None):
    execution._confirmed(confirmed)
    states,_,enrolled,*_=execution.replay(plan,schedule,ledger,
        expected_plan_sha256=expected_plan_sha256,expected_schedule_sha256=expected_schedule_sha256,
        expected_ledger_sha256=expected_ledger_sha256)
    indexed=_truth(plan,truth,expected_plan_sha256)
    if not isinstance(batches,list) or len(batches)>MAX_BATCHES:
        raise ValueError("bounded batch collection required")
    completion_revisions={(e["action"]["episode_id"],e["action"]["arm"]):e["revision"]
                          for e in ledger["events"] if e["action"]["action"]=="completed"}
    seen=set();review_ids=set();reviewed={};pins=[]
    for batch in batches:
        cells,ids,rows=_batch(plan,schedule,ledger,states,enrolled,indexed,batch,completion_revisions)
        if seen&cells or review_ids&ids:
            raise ValueError("attempt or review ID assigned to multiple batches")
        seen.update(cells);review_ids.update(ids);reviewed.update(rows)
        pins.append(batch["expected_batch_sha256"])
    # A completed execution can subsequently lack consent or review material.
    # Do not rewrite execution history or let a withdrawn batch re-enter scoring.
    exclusions={}
    if review_dispositions is None:
        review_dispositions=[]
    if not isinstance(review_dispositions,list) or len(review_dispositions)>len(states):
        raise ValueError("invalid review dispositions")
    for row in review_dispositions:
        cohort.exact(row,("episode_id","arm","status"))
        eid=cohort.token(row["episode_id"]);arm=row["arm"]
        if arm not in cohort.ARMS or eid not in enrolled:
            raise ValueError("unknown review disposition")
        cell=(eid,arm)
        if (row["status"] not in ("withdrawn","review_pending","review_unavailable") or
                cell in exclusions or cell in reviewed or states[cell]["state"]!="completed"):
            raise ValueError("review disposition conflicts with completed review or execution state")
        if row["status"]=="withdrawn" and cell in seen:
            raise ValueError("remove withdrawn material from submitted review batches")
        exclusions[cell]=row["status"]
    runs={a:[] for a in cohort.ARMS};dispositions=[]
    for (eid,arm),state in sorted(states.items()):
        if (eid,arm) in reviewed:
            runs[arm].append(reviewed[eid,arm])
            continue
        raw=state["state"]
        status=("review_pending" if (eid,arm) in seen else "review_unavailable") if raw=="completed" else (
                 "other" if raw=="started" else raw)
        dispositions.append({"episode_id":eid,"arm":arm,"status":exclusions.get((eid,arm),status)})
    result=cohort.score(plan,truth,runs,dispositions,expected_plan_sha256=expected_plan_sha256,confirmed=True)
    body={"format":"owg.pilot-blinded-reconciliation.v1",
          "ledger_sha256":expected_ledger_sha256,"schedule_sha256":expected_schedule_sha256,
          "batch_sha256s":sorted(pins),"completed_attempts":len(completion_revisions),
          "assigned_for_review":len(seen),"reviewed_attempts":len(reviewed),
          "score":result,"blinding_verified":False,"reviewer_identity_verified":False,
          "independent_transport_verified":False,"product_effectiveness_verified":False}
    return {**body,"reconciliation_sha256":cohort.digest(body)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    prep=sub.add_parser("prepare")
    rec=sub.add_parser("reconcile")
    reviewer=sub.add_parser("template")
    reviewer.add_argument("--packet",type=Path,required=True)
    reviewer.add_argument("--out",type=Path,required=True)
    reviewer.add_argument("--confirm",action="store_true",required=True)
    for cmd in (prep,rec):
        execution._arguments(cmd,ledger_required=True)
        cmd.add_argument("--episodes",type=Path,required=True)
    prep.add_argument("--materials",type=Path,required=True)
    prep.add_argument("--private-key",type=Path,required=True)
    rec.add_argument("--batches",type=Path,required=True)
    rec.add_argument("--review-dispositions",type=Path)
    args=parser.parse_args(argv)
    try:
        if args.command=="template":
            cohort._write(args.out,template(cohort._load(args.packet)))
            return 0
        plan=cohort._load(args.plan);schedule=cohort._load(args.schedule)
        ledger=cohort._load(args.ledger);truth=cohort._load_jsonl(args.episodes)
        kw={"expected_plan_sha256":args.plan_sha256,"expected_schedule_sha256":args.schedule_sha256,
            "expected_ledger_sha256":args.ledger_sha256,"confirmed":args.confirm}
        if args.command=="prepare":
            if args.out.resolve()==args.private_key.resolve() or args.out.exists() or args.private_key.exists():
                raise ValueError("use distinct new public-review and private key paths")
            packet,key=prepare(plan,schedule,ledger,truth,cohort._load(args.materials),**kw)
            # Write private key first; if packet creation fails, do not overwrite
            # or silently replace either frozen output on a subsequent command.
            cohort._write(args.private_key,key)
            cohort._write(args.out,packet)
        else:
            cohort._write(args.out,reconcile(plan,schedule,ledger,truth,cohort._load(args.batches),
                            review_dispositions=cohort._load(args.review_dispositions) if args.review_dispositions else None,**kw))
    except (ValueError,TypeError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Blinded pilot operation refused: invalid evidence, pin, or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
