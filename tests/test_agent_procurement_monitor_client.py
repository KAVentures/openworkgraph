"""Portable producer: exact batches, local-only transport and receipt binding."""
from copy import deepcopy
import json

import httpx
import pytest

from shared.agent_procurement import digest
from shared.agent_procurement_monitor_protocol import Assignment, BATCH_FORMAT, INGEST_PATH
from shared import agent_procurement_monitor_client as client

GRANT="1"*24
TOKEN="owgmon_"+GRANT+"."+"A"*64


def assignment(n=3,maximum=2):
    value={"format":"openworkgraph.procurement-monitor-producer-assignment.v1",
        "grant_id":GRANT,"producer_alias":"adapter","window_id":"w1","arm":"challenger",
        "expires_ms":5000,"window_start_ms":1000,"window_end_exclusive_ms":3000,
        "ingest_path":INGEST_PATH,"batch_format":BATCH_FORMAT,"maximum_batch_observations":maximum,
        "execution_authorized":False,"credential_included":False,
        "slots":[{"slot_id":digest(["slot",i]),"case_token_sha256":digest(["case",i]),
                  "expected_router_configuration_sha256":digest("router"),"already_recorded":False}
                 for i in range(n)]}
    return Assignment.model_validate({**value,"assignment_sha256":digest(value)})


def observations(a):
    return [{"slot_id":s.slot_id,"case_token_sha256":s.case_token_sha256,
        "actual_router_configuration_sha256":digest("wrong router"),
        "actual_destination":"manual","actual_candidate_id":None,
        "actual_candidate_configuration_sha256":None,"observed_at_ms":2000,
        "status":"failed","business_outcome":"failure","critical_incident":True,
        "forbidden_action":False,"used_fallback":True,"direct_cost_microunits":100,
        "human_person_ms":36000,"elapsed_ms":500,"source_evidence_sha256":digest(["source",s.slot_id])}
        for s in a.slots]


def test_deterministic_batches_preserve_adverse_evidence():
    a=assignment();rows=observations(a)
    batches=client.prepare(a,rows,now=2500)
    assert batches==client.prepare(a,list(reversed(rows)),now=2500)
    assert [len(x["observations"]) for x in batches]==[2,1]
    assert len({x["batch_id"] for x in batches})==2
    assert {r["slot_id"]:r for b in batches for r in b["observations"]}=={r["slot_id"]:r for r in rows}
    assert TOKEN not in str(batches)


@pytest.mark.parametrize("kind",["slot","case","duplicate","source","future","end","recorded","tampered_manifest"])
def test_scope_and_integrity_refused(kind):
    a=assignment();rows=observations(a)
    if kind=="slot":rows[0]["slot_id"]="f"*64
    elif kind=="case":rows[0]["case_token_sha256"]="f"*64
    elif kind=="duplicate":rows.append(deepcopy(rows[0]))
    elif kind=="source":rows[0]["source_evidence_sha256"]=rows[1]["source_evidence_sha256"]
    elif kind=="future":rows[0]["observed_at_ms"]=2501
    elif kind=="end":rows[0]["observed_at_ms"]=3000
    else:
        raw=a.model_dump();raw["slots"][0]["already_recorded"]=True
        if kind=="tampered_manifest":
            with pytest.raises(ValueError):Assignment.model_validate(raw)
            return
        raw["assignment_sha256"]=digest({k:v for k,v in raw.items() if k!="assignment_sha256"})
        a=Assignment.model_validate(raw)
    with pytest.raises(ValueError):client.prepare(a,rows,now=2500)


@pytest.mark.parametrize("url",["http://example.org:8000","https://127.0.0.1:8000","http://localhost:8000",
    "http://127.0.0.1:8000/a","http://127.0.0.1:8000?x=y","http://user@127.0.0.1:8000",
    "http://127.0.0.1","http://127.0.0.1:0","http://127.0.0.1:65536","http://127.1:8000",
    "http://127.0.0.1:08000","http://127.0.0.1:8000#fragment"])
def test_loopback_only(url):
    with pytest.raises(ValueError):client._url(url)


def test_allowed_loopback():
    assert client._url("http://127.0.0.1:8000")=="http://127.0.0.1:8000"+INGEST_PATH
    assert client._url("http://[::1]:8000/")=="http://[::1]:8000"+INGEST_PATH


def receipt(batch):
    return {"format":"openworkgraph.procurement-monitor-producer-receipt.v1",
        "grant_id":GRANT,"batch_id":batch["batch_id"],"accepted_observations":len(batch["observations"]),
        "idempotent_replay":False,"source_credential_authenticated":True,
        "business_outcomes_independently_verified":False,"execution_authorized":False}


@pytest.mark.parametrize("kind",["ok","redirect","wrong_grant","wrong_batch","wrong_count","oversized","duplicate_key","http_error"])
def test_submission_receipt_and_no_redirect(kind):
    a=assignment();batch=client.prepare(a,observations(a),now=2500)[0];calls=[]
    def handler(req):
        calls.append(req)
        assert req.url.host=="127.0.0.1" and req.url.path==INGEST_PATH
        assert req.headers["authorization"]=="Bearer "+TOKEN
        assert json.loads(req.content)==batch
        value=receipt(batch)
        if kind=="redirect":return httpx.Response(307,headers={"Location":"https://example.org/steal"})
        if kind=="wrong_grant":value["grant_id"]="2"*24
        if kind=="wrong_batch":value["batch_id"]="other"
        if kind=="wrong_count":value["accepted_observations"]=1
        if kind=="oversized":return httpx.Response(200,content=b" "*17000)
        if kind=="duplicate_key":return httpx.Response(200,content=b'{"grant_id":"a","grant_id":"b"}')
        if kind=="http_error":return httpx.Response(409,text=TOKEN)
        return httpx.Response(200,json=value)
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        if kind=="ok":assert client.submit(a,batch,"http://127.0.0.1:8000",TOKEN,client=c,now=2500)==receipt(batch)
        else:
            with pytest.raises(ValueError) as err:client.submit(a,batch,"http://127.0.0.1:8000",TOKEN,client=c,now=2500)
            assert TOKEN not in str(err.value)
    assert len(calls)==1


@pytest.mark.parametrize("kind",["expired","token","changed_batch"])
def test_invalid_submission_never_contacts_server(kind):
    a=assignment();batch=client.prepare(a,observations(a),now=2500)[0]
    now=5000 if kind=="expired" else 2500
    token=TOKEN if kind!="token" else "owgmon_"+"2"*24+"."+"A"*64
    if kind=="changed_batch":batch["observations"][0]["direct_cost_microunits"]+=1
    with httpx.Client(transport=httpx.MockTransport(lambda req:pytest.fail("network call"))) as c:
        with pytest.raises(ValueError):client.submit(a,batch,"http://127.0.0.1:8000",token,client=c,now=now)
