"""Prepare and submit scoped, already-observed monitoring evidence.

This tool does not run agents, poll sources, infer missing outcomes or retry
a submission automatically. Network access is restricted to numeric loopback.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .agent_procurement import digest, loads, MAX_BYTES
from .agent_procurement_monitor_protocol import (
    Assignment, Batch, Receipt, Observation, TOKEN_RE, BATCH_FORMAT, INGEST_PATH,
)
from .agent_procurement_replay import _output

MAX_WIRE_BYTES=512*1024
MAX_RECEIPT_BYTES=16*1024
TOKEN_ENV="OWG_MONITOR_PRODUCER_TOKEN"


def _read(path):
    with path.open("rb") as file:
        return loads(file.read(MAX_BYTES+1))


def _validate(assignment, observations, *, allow_recorded=False, now=None):
    if not isinstance(observations,list) or not 1<=len(observations)<=500:
        raise ValueError("provide a nonempty observation list of at most 500 entries")
    now=int(time.time()*1000) if now is None else now
    slots={s.slot_id:s for s in assignment.slots}
    parsed=[Observation.model_validate(o) for o in observations]
    for key in ("slot_id","case_token_sha256","source_evidence_sha256"):
        if len({getattr(x,key) for x in parsed})!=len(parsed):
            raise ValueError("duplicate submitted evidence")
    for x in parsed:
        slot=slots.get(x.slot_id)
        if slot is None or x.case_token_sha256!=slot.case_token_sha256:
            raise ValueError("observation outside assignment")
        if slot.already_recorded and not allow_recorded:
            raise ValueError("assignment already reports this slot recorded")
        if not assignment.window_start_ms<=x.observed_at_ms<assignment.window_end_exclusive_ms or x.observed_at_ms>now:
            raise ValueError("observation outside assigned time window")
        # Do not 'correct' wrong router versions, destinations or adverse
        # outcomes. Those are exactly the evidence the reviewer needs.
    return sorted((x.model_dump() for x in parsed),key=lambda x:x["slot_id"])


def _batch_id(grant_id,observations):
    return "batch-"+digest([grant_id,observations])


def prepare(assignment,observations,*,now=None):
    rows=_validate(assignment,observations,now=now)
    maximum=assignment.maximum_batch_observations
    batches=[]
    for i in range(0,len(rows),maximum):
        subset=rows[i:i+maximum]
        batches.append(Batch.model_validate({"format":BATCH_FORMAT,
            "batch_id":_batch_id(assignment.grant_id,subset),"observations":subset}).model_dump())
    return batches


def _url(base):
    # Numeric loopback avoids DNS rebinding and accidental transmission to
    # a remote proxy, hostname or provider. No credentials/query/fragment.
    value=urlsplit(base)
    if value.scheme!="http" or value.hostname not in ("127.0.0.1","::1") or value.username is not None or value.password is not None:
        raise ValueError("use an explicit numeric loopback HTTP address")
    if value.path not in ("","/") or value.query or value.fragment or value.port is None or not 1<=value.port<=65535:
        raise ValueError("loopback base URL must include port and no additional path")
    canonical="http://"+("[::1]" if value.hostname=="::1" else "127.0.0.1")+":"+str(value.port)
    if base.rstrip("/")!=canonical:raise ValueError("use canonical numeric loopback address")
    return canonical+INGEST_PATH


def submit(assignment,batch,base,token,*,client=None,now=None):
    now=int(time.time()*1000) if now is None else now
    if now>=assignment.expires_ms:
        raise ValueError("assignment credential has expired")
    if not isinstance(token,str) or not TOKEN_RE.fullmatch(token) or token.split(".",1)[0][7:]!=assignment.grant_id:
        raise ValueError("credential does not match assignment")
    batch=Batch.model_validate(batch)
    rows=_validate(assignment,[x.model_dump() for x in batch.observations],allow_recorded=True,now=now)
    if len(rows)>assignment.maximum_batch_observations or batch.batch_id!=_batch_id(assignment.grant_id,rows):
        raise ValueError("batch does not match deterministic assignment preparation")
    body=json.dumps(batch.model_dump(),ensure_ascii=True,separators=(",",":"),allow_nan=False).encode("ascii")
    if len(body)>MAX_WIRE_BYTES:raise ValueError("batch exceeds wire limit")
    url=_url(base)
    def send(c):
        with c.stream("POST",url,content=body,headers={"Authorization":"Bearer "+token,
                      "Content-Type":"application/json","Accept":"application/json"},follow_redirects=False) as response:
            if response.status_code!=200:
                # Do not expose a server-supplied response body or redirect URL.
                raise ValueError("submission rejected (HTTP "+str(response.status_code)+")")
            data=bytearray()
            for chunk in response.iter_bytes():
                if len(data)+len(chunk)>MAX_RECEIPT_BYTES:raise ValueError("receipt exceeds limit")
                data.extend(chunk)
        receipt=Receipt.model_validate(loads(bytes(data)))
        if receipt.grant_id!=assignment.grant_id or receipt.batch_id!=batch.batch_id or receipt.accepted_observations!=len(rows):
            raise ValueError("receipt does not identify this exact batch")
        return receipt.model_dump()
    if client is not None:return send(client)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=30.0) as c:
        return send(c)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    p=commands.add_parser("prepare")
    p.add_argument("--assignment",type=Path,required=True)
    p.add_argument("--observations",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True)
    s=commands.add_parser("submit")
    s.add_argument("--assignment",type=Path,required=True)
    s.add_argument("--batch",type=Path,required=True)
    s.add_argument("--base-url",required=True)
    s.add_argument("--confirm-submit",action="store_true")
    args=parser.parse_args(argv)
    try:
        assignment=Assignment.model_validate(_read(args.assignment))
        if args.command=="prepare":
            batches=prepare(assignment,_read(args.observations))
            _output(args.out_dir,{"batch-%03d.json"%(i+1):b for i,b in enumerate(batches)})
            print("Prepared",len(batches),"local batches; nothing submitted.")
        else:
            if not args.confirm_submit:raise ValueError("explicit submission confirmation required")
            batch=_read(args.batch)
            _url(args.base_url)
            token=os.environ.get(TOKEN_ENV) or getpass.getpass("Scoped producer credential: ")
            receipt=submit(assignment,batch,args.base_url,token)
            print(json.dumps(receipt,sort_keys=True,separators=(",",":")))
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError,httpx.HTTPError):
        # Pydantic and HTTP exceptions can embed submitted data or credentials.
        # Report only a fixed safe error. A network failure may have happened
        # after commit: keep the exact batch and explicitly retry it.
        parser.exit(2,"Producer operation failed. No success is established; check scope, expiry and local server. Keep the exact batch for explicit retry.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
