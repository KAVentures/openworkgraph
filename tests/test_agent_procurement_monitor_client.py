"""Portable client tests: real manifest/producer integration and mock transport."""
from copy import deepcopy
import json

import httpx
import pytest

from shared import agent_procurement_monitor_client as client
from shared.agent_procurement_monitor_protocol import Assignment
from shared.agent_procurement import digest
from server import agent_procurement_monitor_telemetry as telemetry
from server import agent_procurement_monitor_journal as journal
from test_agent_procurement_monitor_telemetry import producer, credential
from test_agent_procurement_monitor_journal import local_db, monitor, pilot, version_change, rehearsal, trial, study

BASE="http://127.0.0.1:8000"


def assignment(producer):
    grant,batch,clock,_=producer
    return Assignment.model_validate(telemetry.manifest("process_1",1,{
        "monitor_id":"monitor-1","expected_head_sha256":grant["head_sha256"],
        "grant_id":grant["grant"]["grant_id"],"confirmed":True}))


def test_native_client_round_trip_and_exact_replay(producer):
    grant,batch,clock,_=producer
    a=assignment(producer)
    rows=batch["observations"]
    prepared=client.prepare(a,rows,now=clock[0])
    assert len(prepared)==1
    assert client.prepare(a,list(reversed(rows)),now=clock[0])==prepared
    calls=[]
    def handler(request):
        calls.append(request)
        assert str(request.url)==BASE+a.ingest_path
        assert request.headers["authorization"]=="Bearer "+grant["producer_token"]
        assert "origin" not in request.headers
        receipt=telemetry.ingest(credential(grant),json.loads(request.content))
        return httpx.Response(200,json=receipt)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        first=client.submit(a,prepared[0],BASE,grant["producer_token"],client=transport,now=clock[0])
        repeated=client.submit(a,prepared[0],BASE,grant["producer_token"],client=transport,now=clock[0])
    assert first["accepted_observations"]==len(rows) and not first["idempotent_replay"]
    assert repeated["idempotent_replay"] and len(calls)==2
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})["checkpoint_count"]==2


def test_preparation_preserves_adverse_measurement_and_scope(producer):
    grant,batch,clock,_=producer
    a=assignment(producer)
    rows=deepcopy(batch["observations"])
    rows[0]["actual_router_configuration_sha256"]=digest("unexpected router")
    rows[0]["critical_incident"]=True
    prepared=client.prepare(a,rows,now=clock[0])
    assert next(o for o in prepared[0]["observations"] if o["slot_id"]==rows[0]["slot_id"])==rows[0]
    for field,value in (("slot_id",digest("other slot")),("case_token_sha256",digest("other case")),
                        ("observed_at_ms",a.window_end_exclusive_ms)):
        bad=deepcopy(rows);bad[0][field]=value
        with pytest.raises(ValueError):client.prepare(a,bad,now=clock[0])
    with pytest.raises(ValueError):client.prepare(a,rows+[rows[0]],now=clock[0])
    raw=a.model_dump();raw["slots"][0]["already_recorded"]=True
    with pytest.raises(ValueError):Assignment.model_validate(raw)
    raw["assignment_sha256"]=digest({k:v for k,v in raw.items() if k!="assignment_sha256"})
    with pytest.raises(ValueError):client.prepare(Assignment.model_validate(raw),rows,now=clock[0])


@pytest.mark.parametrize("base",["http://localhost:8000","https://127.0.0.1:8000",
    "http://127.0.0.2:8000","http://127.0.0.1","http://127.0.0.1:8000/path",
    "http://127.0.0.1:8000?x=1","http://user@127.0.0.1:8000","http://127.0.0.1:8000#x",
    "http://2130706433:8000","http://127.0.0.1:08000","http://[::ffff:127.0.0.1]:8000"])
def test_remote_and_noncanonical_urls_refused(base):
    with pytest.raises(ValueError):client._url(base)


@pytest.mark.parametrize("kind",["redirect","wrong_grant","wrong_count","wrong_batch","extra","oversized","duplicate_key","http_error"])
def test_bad_receipts_refused_without_retry_or_redirect(producer,kind):
    grant,batch,clock,_=producer
    a=assignment(producer);b=client.prepare(a,batch["observations"],now=clock[0])[0]
    count=0
    def handler(request):
        nonlocal count
        count+=1
        if kind=="redirect":return httpx.Response(307,headers={"Location":"http://example.test/secret"})
        if kind=="http_error":return httpx.Response(503,text="PRIVATE_SERVER_ERROR")
        if kind=="oversized":return httpx.Response(200,content=b"x"*(client.MAX_RECEIPT_BYTES+1))
        if kind=="duplicate_key":return httpx.Response(200,content=b'{"grant_id":"a","grant_id":"b"}')
        result=telemetry._receipt(a.grant_id,b["batch_id"],len(b["observations"]))
        if kind=="wrong_grant":result["grant_id"]="f"*24
        elif kind=="wrong_count":result["accepted_observations"]-=1
        elif kind=="wrong_batch":result["batch_id"]="other"
        else:result["unexpected"]="PRIVATE_SERVER_ERROR"
        return httpx.Response(200,json=result)
    with httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=True) as transport:
        with pytest.raises(ValueError):client.submit(a,b,BASE,grant["producer_token"],client=transport,now=clock[0])
    assert count==1


def test_expiry_and_grant_mismatch_fail_before_network(producer):
    grant,batch,clock,_=producer
    a=assignment(producer);b=client.prepare(a,batch["observations"],now=clock[0])[0]
    def handler(request):pytest.fail("unexpected network submission")
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        for token,now in ((grant["producer_token"],a.expires_ms),("owgmon_"+"f"*24+"."+"A"*64,clock[0])):
            with pytest.raises(ValueError):client.submit(a,b,BASE,token,client=transport,now=now)
        b["batch_id"]="changed"
        with pytest.raises(ValueError):client.submit(a,b,BASE,grant["producer_token"],client=transport,now=clock[0])
