"""Customer-operated loopback replay collection and hash-chained attempt receipts.

Explicit local execution only. The collector observes transport and elapsed
time, not the remote agent's behavior, identity, billing or sandbox isolation.
No model credentials or business permissions are supplied. Each shard writes
a private append-only journal; a started slot is never automatically retried.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from .agent_procurement import Alias, Cost, Duration, Strict, _cohort, _load, digest, loads, MAX_BYTES
from .agent_procurement_replay import RawAttempt, RawLedger, RAW_CAPTURES, _evaluate, _output, score
from .agent_procurement_trials import _validate_plan
from .process_agent_project import build_runtime_spec
from .process_shadow_review import Hash

ADAPTERS = "openworkgraph.procurement-loopback-adapters.v1"
JOURNAL = "openworkgraph.procurement-attempt-journal.v1"
REQUEST = "openworkgraph.procurement-http-request.v1"
MAX_SHARD = 100
MAX_JOURNAL = 24 * 1024 * 1024
MAX_RAW_SHARD = 4 * 1024 * 1024
MAX_JOURNALS = 100
MAX_RECORD_BYTES = 256 * 1024
MAX_COMBINED_JOURNAL_BYTES = 32 * 1024 * 1024
ZERO = "0" * 64


class Adapter(Strict):
    candidate_id: Alias
    port: Annotated[StrictInt, Field(ge=1024,le=65535)]
    path: Annotated[str, Field(pattern=r"^/[A-Za-z0-9_/-]{1,160}$")]
    timeout_ms: Annotated[StrictInt, Field(ge=100,le=120000)]
    backend_configuration_sha256: Hash

    @model_validator(mode="after")
    def path_is_literal(self):
        if "//" in self.path:
            raise ValueError("use a literal loopback endpoint path")
        return self


class Adapters(Strict):
    format: Literal["openworkgraph.procurement-loopback-adapters.v1"]
    adapters: list[Adapter] = Field(min_length=2,max_length=10)


class TransportResult(Strict):
    status: Literal["completed","timeout","transport_failed","invalid_output"]
    http_status: Annotated[StrictInt,Field(ge=100,le=599)] | None
    response_sha256: Hash | None
    response_bytes: Annotated[StrictInt,Field(ge=0,le=128*1024)] | None
    cost_microunits: Cost | None
    reason: Literal["connection_or_protocol","non_200_status","response_too_large",
                    "invalid_response_envelope","response_received","socket_timeout",
                    "wall_timeout","collector_child_failed"]
    plan: object = None

    @model_validator(mode="after")
    def consistent(self):
        delivered=self.status=="completed"
        if delivered != ("plan" in self.model_fields_set):
            raise ValueError("transport completion and plan presence differ")
        if (self.response_sha256 is None)!=(self.response_bytes is None):
            raise ValueError("response hash and byte count must agree")
        if delivered and (self.http_status!=200 or self.response_sha256 is None or
                          self.reason!="response_received"):
            raise ValueError("completed transport requires response metadata")
        if not delivered and self.cost_microunits is not None:
            raise ValueError("failed transport has unknown cost")
        allowed={"completed":{"response_received"},"timeout":{"socket_timeout","wall_timeout"},
                 "transport_failed":{"connection_or_protocol","non_200_status","collector_child_failed"},
                 "invalid_output":{"response_too_large","invalid_response_envelope"}}
        if self.reason not in allowed[self.status]:
            raise ValueError("transport disposition and reason differ")
        return self


def adapter_digest(adapter):
    """Configuration pin includes the exact literal endpoint and timeout."""
    return digest(Adapter.model_validate(adapter).model_dump())


def _context(packet,suite,plan,adapters):
    design,verified=_validate_plan(packet,plan)
    reference,_=_evaluate(packet,suite,[])
    frozen=design.reference_report
    if (reference.spec_sha256!=frozen.spec_sha256 or reference.suite_sha256!=frozen.suite_sha256
            or _cohort(reference)!=_cohort(frozen)):
        raise ValueError("suite differs from the frozen plan")
    configured=Adapters.model_validate(adapters)
    by_id={a.candidate_id:a for a in configured.adapters}
    candidates={c.candidate_id:c for c in design.candidates}
    if len(by_id)!=len(configured.adapters) or set(by_id)!=set(candidates):
        raise ValueError("supply exactly one adapter for every frozen candidate")
    for cid,a in by_id.items():
        if adapter_digest(a.model_dump())!=candidates[cid].configuration_sha256:
            raise ValueError("adapter configuration differs from frozen candidate")
    cases={c["id"]:c["input"] for c in suite["cases"]}
    return verified,design,by_id,cases,build_runtime_spec(packet)


def _request(plan,slot,case,spec,config):
    # Only the assigned case. No other candidate, gate, category, expected
    # answer, reference score or entire answer-bearing suite is supplied.
    return {"format":REQUEST,"plan_sha256":plan["plan_sha256"],"slot_id":slot["slot_id"],
            "candidate_id":slot["candidate_id"],"configuration_sha256":adapter_digest(config.model_dump()),
            "repeat":slot["repeat"],"case_id":slot["case_id"],"input":case,
            "process_spec":spec,"execution_authorized":False,"business_tools_authorized":False}


def _failure(status,reason):
    return {"status":status,"http_status":None,"response_sha256":None,"response_bytes":None,
            "cost_microunits":None,"reason":reason}


def _invoke(adapter,request):
    """A fixed child provides a hard parent deadline, including slow headers."""
    payload=json.dumps({"port":adapter.port,"path":adapter.path,"timeout_ms":adapter.timeout_ms,
                        "request":request},separators=(",",":"),allow_nan=False).encode()
    helper=Path(__file__).with_name("agent_procurement_http_transport.py")
    env={k:os.environ[k] for k in ("SystemRoot","WINDIR","TEMP","TMP","TMPDIR") if k in os.environ}
    start=time.monotonic_ns()
    try:
        process=subprocess.run([sys.executable,"-I","-B",str(helper)],input=payload,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=adapter.timeout_ms/1000)
        if process.returncode!=0 or len(process.stdout)>256*1024:
            result=_failure("transport_failed","collector_child_failed")
        else:
            result=TransportResult.model_validate(loads(process.stdout)).model_dump(exclude_unset=True)
    except subprocess.TimeoutExpired:
        result=_failure("timeout","wall_timeout")
    except (OSError,ValueError,TypeError,RecursionError):
        result=_failure("transport_failed","collector_child_failed")
    elapsed=min(86_400_000,max(0,(time.monotonic_ns()-start+999_999)//1_000_000))
    return result,elapsed


def _stamp():
    return datetime.now(timezone.utc).isoformat()


class _Writer:
    def __init__(self,path):
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        self.stream=os.fdopen(fd,"wb")
        self.previous=ZERO
        self.count=0

    def write(self,event):
        body={"sequence":self.count,"previous_sha256":self.previous,"event":event}
        record={**body,"record_sha256":digest(body)}
        self.stream.write(json.dumps(record,separators=(",",":"),allow_nan=False).encode()+b"\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.previous=record["record_sha256"]
        self.count+=1

    def close(self):
        self.stream.close()


def run_shard(packet,suite,plan,adapters,directory,*,offset,count,confirm_local_endpoint=False,
              previous_journals=(),expected_previous_heads=None):
    if confirm_local_endpoint is not True:
        raise ValueError("explicit confirmation of local sandbox endpoints and case submission is required")
    verified,design,by_id,cases,spec=_context(packet,suite,plan,adapters)
    if (type(offset) is not int or type(count) is not int or offset<0 or
            not 1<=count<=MAX_SHARD or offset+count>len(verified["slots"])):
        raise ValueError("choose a bounded contiguous shard of frozen slots")
    if expected_previous_heads is not None and not previous_journals:
        raise ValueError("head pins require the previous journal files")
    if previous_journals:
        _, evidence=collect(packet,suite,verified,adapters,previous_journals,
                            expected_heads=expected_previous_heads)
        used={row["slot_id"] for row in evidence["receipts"]}
        if any(s["slot_id"] in used for s in verified["slots"][offset:offset+count]):
            raise ValueError("selected shard overlaps a previously started slot; no requests submitted")
    # Preflight every selected request before opening the journal or making a
    # network request. No partial execution due to an oversized later case.
    prepared=[]
    for slot in verified["slots"][offset:offset+count]:
        adapter=by_id[slot["candidate_id"]]
        request=_request(verified,slot,cases[slot["case_id"]],spec,adapter)
        if len(json.dumps(request,sort_keys=True,separators=(",",":"),allow_nan=False).encode())>1024*1024:
            raise ValueError("selected request exceeds 1 MiB")
        prepared.append((slot,adapter,request))
    directory.mkdir(mode=0o700,parents=False,exist_ok=False)
    writer=_Writer(directory/"attempts.private.jsonl")
    raw_bytes=0
    try:
        writer.write({"type":"header","format":JOURNAL,"plan_sha256":verified["plan_sha256"],
                      "offset":offset,"count":count,"created_at":_stamp()})
        for slot,adapter,request in prepared:
            # Write and fsync before any request; crash/interrupt leaves an
            # unresolved started slot. A new shard may not overlap it.
            writer.write({"type":"started","slot_id":slot["slot_id"],"request_sha256":digest(request),
                          "configuration_sha256":adapter_digest(adapter.model_dump()),"at":_stamp()})
            result,elapsed=_invoke(adapter,request)
            writer.write({"type":"finished","slot_id":slot["slot_id"],"at":_stamp(),
                          "elapsed_ms":elapsed,"transport":result})
            raw_bytes+=len(json.dumps(result,separators=(",",":"),allow_nan=False).encode())
            if raw_bytes>=MAX_RAW_SHARD:
                break
    finally:
        writer.close()
    return {"journal":str(directory/"attempts.private.jsonl"),"chain_head_sha256":writer.previous,
            "records":writer.count,"planned_shard_slots":count,"execution_authorized":False}


def _read_records(path):
    previous=ZERO
    size=0
    with path.open("rb") as handle:
        sequence=0
        while line:=handle.readline(MAX_RECORD_BYTES+1):
            size+=len(line)
            if (len(line)>MAX_RECORD_BYTES or size>MAX_JOURNAL or sequence>2*MAX_SHARD
                    or not line.endswith(b"\n")):
                raise ValueError("journal exceeds bounds or has an incomplete trailing record")
            row=loads(line)
            if not isinstance(row,dict) or set(row)!={"sequence","previous_sha256","event","record_sha256"}:
                raise ValueError("invalid journal record")
            body={k:row[k] for k in ("sequence","previous_sha256","event")}
            if type(row["sequence"]) is not int or row["sequence"]!=sequence or row["previous_sha256"]!=previous or digest(body)!=row["record_sha256"]:
                raise ValueError("journal chain mismatch")
            previous=row["record_sha256"]
            yield row["event"],previous
            sequence+=1


def _time(value):
    if not isinstance(value,str) or len(value)>64 or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError("invalid observation timestamp")


def _keys(event,keys):
    if not isinstance(event,dict) or set(event)!=keys:
        raise ValueError("invalid journal event")


def collect(packet,suite,plan,adapters,journals,*,expected_heads=None):
    """Validate exact disjoint journal shards and export raw ledger + receipts.

    Started but unfinished slots stay unresolved. Hash chains identify content,
    not an independently authenticated history or proof of no missing journals.
    """
    verified,design,by_id,cases,spec=_context(packet,suite,plan,adapters)
    if not 1<=len(journals)<=MAX_JOURNALS:
        raise ValueError("select 1 to 100 journal files")
    if sum(p.stat().st_size for p in journals)>MAX_COMBINED_JOURNAL_BYTES:
        raise ValueError("combined journal input exceeds 32 MiB")
    if expected_heads is not None:
        if (not isinstance(expected_heads,dict) or set(expected_heads)!={"format","plan_sha256","chain_heads_sha256"}
                or expected_heads["format"]!="openworkgraph.procurement-journal-heads.v1"
                or expected_heads["plan_sha256"]!=verified["plan_sha256"]
                or not isinstance(expected_heads["chain_heads_sha256"],list)
                or len(expected_heads["chain_heads_sha256"])!=len(journals)
                or any(not isinstance(h,str) or len(h)!=64 or any(c not in "0123456789abcdef" for c in h)
                       for h in expected_heads["chain_heads_sha256"])):
            raise ValueError("invalid expected journal head manifest")
    seen=set();grouped=defaultdict(list);receipts=[];chains=[];combined_attempt_bytes=0
    for path in journals:
        records=iter(_read_records(path))
        try:header,head=next(records)
        except StopIteration:raise ValueError("empty attempt journal") from None
        _keys(header,{"type","format","plan_sha256","offset","count","created_at"})
        offset,count=header["offset"],header["count"]
        if (header["type"]!="header" or header["format"]!=JOURNAL or header["plan_sha256"]!=verified["plan_sha256"]
                or type(offset) is not int or type(count) is not int or offset<0 or not 1<=count<=MAX_SHARD
                or offset+count>len(verified["slots"])):
            raise ValueError("journal does not identify a valid frozen shard")
        _time(header["created_at"])
        slots=verified["slots"][offset:offset+count]
        started=None;position=0
        for event,head in records:
            if not isinstance(event,dict):raise ValueError("invalid journal event")
            if event.get("type")=="started":
                _keys(event,{"type","slot_id","request_sha256","configuration_sha256","at"})
                if started is not None or position>=len(slots):
                    raise ValueError("journal start order is invalid")
                slot=slots[position];sid=slot["slot_id"]
                adapter=by_id[slot["candidate_id"]]
                request=_request(verified,slot,cases[slot["case_id"]],spec,adapter)
                if (sid in seen or event["slot_id"]!=sid or event["request_sha256"]!=digest(request)
                        or event["configuration_sha256"]!=adapter_digest(adapter.model_dump())):
                    raise ValueError("overlapping or unbound journal attempt")
                _time(event["at"]);seen.add(sid);started=(slot,event,head)
            elif event.get("type")=="finished":
                _keys(event,{"type","slot_id","at","elapsed_ms","transport"})
                if started is None or event["slot_id"]!=started[0]["slot_id"]:
                    raise ValueError("journal terminal record lacks matching start")
                slot,start,start_hash=started
                _time(event["at"])
                transport=TransportResult.model_validate(event["transport"])
                raw={"case_id":slot["case_id"],"slot_id":slot["slot_id"],"status":transport.status,
                     "cost_microunits":transport.cost_microunits,"elapsed_ms":event["elapsed_ms"]}
                if transport.status=="completed":raw["plan"]=transport.plan
                attempt=RawAttempt.model_validate(raw).model_dump(exclude_unset=True)
                combined_attempt_bytes+=len(json.dumps(attempt,allow_nan=False,indent=2).encode())
                if combined_attempt_bytes>MAX_BYTES:
                    raise ValueError("combined raw attempt payload exceeds the supported limit")
                grouped[(slot["candidate_id"],slot["repeat"])].append(attempt)
                receipts.append({**slot,"status":transport.status,"request_sha256":start["request_sha256"],
                                 "configuration_sha256":start["configuration_sha256"],"started_at":start["at"],
                                 "finished_at":event["at"],"elapsed_ms":attempt["elapsed_ms"],
                                 "cost_microunits":transport.cost_microunits,"cost_origin":"endpoint_declaration",
                                 "http_status":transport.http_status,"response_sha256":transport.response_sha256,
                                 "response_bytes":transport.response_bytes,"reason":transport.reason,
                                 "start_record_sha256":start_hash,"terminal_record_sha256":head,
                                 "returned_plan_sha256":digest(transport.plan) if transport.status=="completed" else None})
                position+=1;started=None
            else:raise ValueError("unknown journal event")
        if started is not None:
            slot,start,start_hash=started
            receipts.append({**slot,"status":"started_unfinished","request_sha256":start["request_sha256"],
                             "configuration_sha256":start["configuration_sha256"],"started_at":start["at"],
                             "start_record_sha256":start_hash,"terminal_record_sha256":None})
        chains.append({"offset":offset,"count":count,"terminal_slots":position,
                       "started_unfinished":started is not None,"chain_head_sha256":head})
    chain_heads=sorted(c["chain_head_sha256"] for c in chains)
    if len(set(chain_heads))!=len(chain_heads):
        raise ValueError("duplicate journal chain")
    if expected_heads is not None and chain_heads!=sorted(expected_heads["chain_heads_sha256"]):
        raise ValueError("journal chain heads differ from the pinned manifest")
    head_manifest={"format":"openworkgraph.procurement-journal-heads.v1",
                   "plan_sha256":verified["plan_sha256"],"chain_heads_sha256":chain_heads}
    captures=[]
    for (cid,repeat),attempts in sorted(grouped.items()):
        captures.append({"candidate_id":cid,"repeat":repeat,
                         "configuration_sha256":adapter_digest(by_id[cid].model_dump()),
                         "attempts":sorted(attempts,key=lambda a:a["case_id"])})
    ledger={"format":RAW_CAPTURES,"plan_sha256":verified["plan_sha256"],"captures":captures}
    RawLedger.model_validate(ledger)
    if len((json.dumps(ledger,allow_nan=False,indent=2)+"\n").encode())>MAX_BYTES:
        raise ValueError("combined raw ledger exceeds 8 MiB; reduce trial size or returned plan length")
    evidence={"format":"openworkgraph.procurement-attempt-receipts.v1","plan_sha256":verified["plan_sha256"],
              "raw_capture_sha256":digest(ledger),"scheduled_attempts":len(verified["slots"]),
              "started_attempts":len(seen),"terminal_attempts":sum(c["terminal_slots"] for c in chains),
              "unstarted_attempts":len(verified["slots"])-len(seen),"journal_chains":chains,
              "receipts":sorted(receipts,key=lambda r:(r["case_id"],r["repeat"],r["position"])),
              "receipt_chain_consistency_checked":True,"receipt_authenticity_verified":False,
              "journal_head_manifest":head_manifest,"externally_supplied_head_pins_matched":expected_heads is not None,
              "elapsed_time_basis":"collector_wall_time_including_transport_child_overhead",
              "provider_cost_verified":False,"external_agent_identity_verified":False,
              "endpoint_sandbox_verified":False,"execution_authorized":False}
    return ledger,evidence


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="operation",required=True)
    for name in ("run","collect"):
        p=sub.add_parser(name)
        for argument in ("process-export","suite","plan","adapters","out-dir"):
            p.add_argument("--"+argument,type=Path,required=True)
        if name=="run":
            p.add_argument("--offset",type=int,required=True)
            p.add_argument("--count",type=int,required=True)
            p.add_argument("--confirm-local-endpoint",action="store_true")
            p.add_argument("--previous-journal",type=Path,action="append",default=[])
        else:p.add_argument("--journal",type=Path,action="append",required=True)
        p.add_argument("--expected-heads",type=Path,help="Previously retained exact journal head manifest")
    args=parser.parse_args(argv)
    try:
        packet,suite,plan,adapters=(_load(p) for p in (args.process_export,args.suite,args.plan,args.adapters))
        heads=_load(args.expected_heads) if args.expected_heads else None
        if args.operation=="run":
            result=run_shard(packet,suite,plan,adapters,args.out_dir,offset=args.offset,count=args.count,
                             confirm_local_endpoint=args.confirm_local_endpoint,previous_journals=args.previous_journal,
                             expected_previous_heads=heads)
            print(json.dumps(result))
        else:
            ledger,receipts=collect(packet,suite,plan,adapters,args.journal,expected_heads=heads)
            minimized,result=score(packet,suite,plan,ledger)
            result.update(local_transport_receipts_sha256=digest(receipts),
                          local_transport_receipt_consistency_checked=True,
                          provider_cost_verified=False,external_candidate_execution_observed=False)
            _output(args.out_dir,{"captures.raw.private.json":ledger,"captures.minimized.json":minimized,
                                  "receipts.json":receipts,"journal-heads.json":receipts["journal_head_manifest"],"review.json":result})
    except (OSError,ValueError,KeyError,TypeError,RecursionError,OverflowError):
        parser.exit(2,"Replay collection stopped or evidence was incompatible. Inspect any retained private journal; do not blindly retry a started slot.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
