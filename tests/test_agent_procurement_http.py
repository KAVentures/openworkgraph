"""Actual loopback protocol, frozen configuration and crash-safe receipts."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_replay import prepare,score
from shared.agent_procurement_http import adapter_digest,run_shard,collect,_Writer,_invoke,Adapter
from test_agent_procurement_replay import inputs


@pytest.fixture
def endpoint():
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            value=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(value)
            if self.path=="/redirect":
                self.send_response(302);self.send_header("Location","http://127.0.0.1:1/");self.end_headers();return
            if self.path=="/invalid":
                raw=b'{"format":"wrong"}'
            else:
                spec=value["process_spec"]
                plan={"revision":spec["revision"],"content_sha256":spec["content_sha256"],
                      "status":"proposal" if value["case_id"]=="case_1" else "needs_clarification",
                      "activity_ids":["a","b"] if value["case_id"]=="case_1" else [],
                      "missing_business_inputs":[] if value["case_id"]=="case_1" else ["PRIVATE_QUESTION"]}
                raw=json.dumps({"format":"openworkgraph.procurement-http-response.v1","slot_id":value["slot_id"],
                                "plan":plan,"cost_microunits":123}).encode()
            self.send_response(200);self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server.server_port,requests
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


@pytest.fixture
def bound(inputs,endpoint):
    packet,suite,design=inputs
    port,requests=endpoint
    configs=[{"candidate_id":cid,"port":port,"path":"/plan","timeout_ms":3000,
              "backend_configuration_sha256":digest(cid)} for cid in ("current","other")]
    adapters={"format":"openworkgraph.procurement-loopback-adapters.v1","adapters":configs}
    for candidate,config in zip(design["candidates"],configs):
        candidate["configuration_sha256"]=adapter_digest(config)
    plan,_=prepare(packet,suite,design,confirm_export_inputs=True)
    return packet,suite,plan,adapters,requests


def test_live_loopback_shards_and_scoring(bound,tmp_path):
    packet,suite,plan,adapters,requests=bound
    first=tmp_path/"first";second=tmp_path/"second"
    run_shard(packet,suite,plan,adapters,first,offset=0,count=2,confirm_local_endpoint=True)
    run_shard(packet,suite,plan,adapters,second,offset=2,count=2,confirm_local_endpoint=True)
    assert len(requests)==4
    assert [r["slot_id"] for r in requests]==[s["slot_id"] for s in plan["slots"]]
    assert all("cases" not in r and "expected" not in r and "category" not in r and "gates" not in r for r in requests)
    assert all(r["business_tools_authorized"] is False for r in requests)
    paths=[first/"attempts.private.jsonl",second/"attempts.private.jsonl"]
    ledger,evidence=collect(packet,suite,plan,adapters,paths)
    assert evidence["terminal_attempts"]==4 and evidence["unstarted_attempts"]==0
    assert evidence["receipt_chain_consistency_checked"] and not evidence["receipt_authenticity_verified"]
    assert evidence["raw_capture_sha256"]==digest(ledger)
    assert all(r["cost_microunits"]==123 and r["elapsed_ms"]>=0 for r in evidence["receipts"])
    assert "PRIVATE_" not in json.dumps(evidence)
    _,review=score(packet,suite,plan,ledger)
    assert all(c["match_bounds"]["lower"]==2 and c["cost"]["total_microunits"]==246 for c in review["candidates"])
    with pytest.raises(ValueError,match="overlapping"):collect(packet,suite,plan,adapters,[paths[0],paths[0]])
    with pytest.raises(FileExistsError):
        run_shard(packet,suite,plan,adapters,first,offset=0,count=1,confirm_local_endpoint=True)


def test_preflight_configuration_and_confirmation_no_network(bound,tmp_path):
    packet,suite,plan,adapters,requests=bound
    with pytest.raises(ValueError,match="confirmation"):
        run_shard(packet,suite,plan,adapters,tmp_path/"not-created",offset=0,count=1)
    changed=deepcopy(adapters);changed["adapters"][0]["path"]="/different"
    with pytest.raises(ValueError,match="configuration"):
        run_shard(packet,suite,plan,changed,tmp_path/"not-created",offset=0,count=1,confirm_local_endpoint=True)
    assert not requests and not (tmp_path/"not-created").exists()


def test_interrupted_attempt_is_unresolved_and_cannot_be_replayed_in_collection(bound,tmp_path,monkeypatch):
    packet,suite,plan,adapters,requests=bound
    def interrupt(*args):raise KeyboardInterrupt()
    monkeypatch.setattr("shared.agent_procurement_http._invoke",interrupt)
    first=tmp_path/"interrupted"
    with pytest.raises(KeyboardInterrupt):
        run_shard(packet,suite,plan,adapters,first,offset=0,count=2,confirm_local_endpoint=True)
    path=first/"attempts.private.jsonl"
    ledger,evidence=collect(packet,suite,plan,adapters,[path])
    assert not ledger["captures"] and evidence["started_attempts"]==1 and evidence["terminal_attempts"]==0
    assert evidence["receipts"][0]["status"]=="started_unfinished"
    assert evidence["unstarted_attempts"]==3
    with pytest.raises(ValueError,match="overlapping"):
        collect(packet,suite,plan,adapters,[path,path])
    assert not requests


def test_chain_modification_and_truncated_record_refused(bound,tmp_path):
    packet,suite,plan,adapters,_=bound
    directory=tmp_path/"shard"
    run_shard(packet,suite,plan,adapters,directory,offset=0,count=1,confirm_local_endpoint=True)
    path=directory/"attempts.private.jsonl";raw=path.read_bytes()
    path.write_bytes(raw.replace(b'"elapsed_ms":',b'"elapsed_bad":'))
    with pytest.raises(ValueError,match="chain mismatch"):collect(packet,suite,plan,adapters,[path])
    path.write_bytes(raw[:-1])
    with pytest.raises(ValueError,match="incomplete"):collect(packet,suite,plan,adapters,[path])


@pytest.mark.parametrize("path,status,reason",[("/redirect","transport_failed","non_200_status"),
                                               ("/invalid","invalid_output","invalid_response_envelope")])
def test_transport_never_follows_redirect_or_accepts_invalid_output(endpoint,path,status,reason):
    port,requests=endpoint
    adapter=Adapter(candidate_id="test",port=port,path=path,timeout_ms=3000,backend_configuration_sha256="a"*64)
    result,elapsed=_invoke(adapter,{"slot_id":"a"*64})
    assert result["status"]==status and result["reason"]==reason
    assert result["cost_microunits"] is None and "plan" not in result and elapsed>=0
    assert len(requests)==1


def test_hard_timeout_observed_and_no_retry(bound,tmp_path,monkeypatch):
    import subprocess
    packet,suite,plan,adapters,_=bound
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired("fixed child",1)
    # Only patch the transport after context validation (the trusted offline
    # evaluator also uses subprocess.run).
    from shared import agent_procurement_http as harness
    original=harness.subprocess.run
    calls=[]
    def dispatch(args,*a,**kw):
        if "agent_procurement_http_transport.py" in str(args):
            calls.append(args);return timeout(*a,**kw)
        return original(args,*a,**kw)
    monkeypatch.setattr(harness.subprocess,"run",dispatch)
    directory=tmp_path/"timeout"
    run_shard(packet,suite,plan,adapters,directory,offset=0,count=1,confirm_local_endpoint=True)
    ledger,evidence=collect(packet,suite,plan,adapters,[directory/"attempts.private.jsonl"])
    assert len(calls)==1
    assert ledger["captures"][0]["attempts"][0]["status"]=="timeout"
    assert evidence["receipts"][0]["reason"]=="wall_timeout"
