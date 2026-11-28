"""Explicit finance-source read, exact minor-unit reconciliation, no default disclosure."""
from copy import deepcopy
from itertools import product

import pytest

from shared.agent_procurement_source_finance import compare,summarize
from server import agent_procurement_source_finance as finance_source
from server import agent_procurement_finance as finance
from server import agent_procurement_source_outcome as source
from server import agent_procurement_monitor_journal as journal
from server.stripe_source_evidence import stripe_record_receipt
from test_agent_procurement_monitor_journal import local_db,doc,monitor,pilot,version_change,rehearsal,trial,study
from test_agent_procurement_finance import request as finance_request
from test_agent_procurement_source_outcome import receipt,REF,RECORD,pins,bind_doc,install_record_events


def money(amount="100",currency="USD",live=True):
    return {**receipt(),"invoice_finance":{"amount_paid_minor_units":amount,
        "currency":currency,"live_record_reported":live}}


def test_amounts_exact_and_no_unknown_as_zero():
    for raw,live,cur,declared,prior in product(
            (None,True,0,"0","1","01","-1","1.0","1e3","1000000000000000000","1000000000000000001"),
            (True,False,1),("USD","SEK",None),(0,1,10**18),(1000,2000,3000)):
        r=money(raw,cur,live);old=deepcopy(r)
        actual=compare(r,2000,3000,5000,prior,"USD",declared)
        valid=live is True and cur=="USD" and prior==2000 and raw in ("0","1","1000000000000000000")
        assert actual["status"]==(("match" if int(raw)==declared else "mismatch") if valid else "unknown")
        if valid:assert int(actual["difference_minor_units"])==int(raw)-declared
        else:assert actual["source_amount_paid_minor_units"] is None and actual["difference_minor_units"] is None
        assert r==old


def test_totals_do_not_combine_currencies_or_count_retries():
    def row(slot,currency,arm,status,amount):
        return {"slot_id":slot,"window_id":"w1","currency":currency,"arm":arm,
                "reconciliation_sha256":"a"*64,"status":status,
                "declared_amount_paid_minor_units":"100",
                "source_amount_paid_minor_units":amount}
    state={"source_finance_checks":[row("a","USD","incumbent","unknown",None),
        row("a","USD","incumbent","match","100"),row("b","USD","incumbent","unknown",None),
        row("c","SEK","challenger","mismatch","110")]}
    result=summarize(state)
    assert (result["selected"],result["matched"],result["mismatched"],result["unknown"])==(3,1,1,1)
    assert result["groups"][0]["known_difference_minor_units"]=="10"
    assert result["groups"][1]["known_declared_minor_units"]=="100"
    assert not result["complete_ledger_verified"] and not result["bank_settlement_verified"]


@pytest.mark.parametrize("key,livemode,amount,currency,valid",[
    ("rk_live_example",True,123,"usd",True),
    ("rk_test_example",False,123,"usd",False),
    ("rk_test_example",True,123,"usd",False),
    ("rk_live_example",False,123,"usd",False),
    ("rk_live_example",True,True,"usd",False),
    ("rk_live_example",True,-1,"usd",False),
    ("rk_live_example",True,123,"USD",False)])
def test_adapter_default_no_amounts_and_explicit_live_projection(key,livemode,amount,currency,valid):
    account="acct_1234567890ABCDEF"
    native=RECORD["resolver_locator"].split(":")[1]
    env={"OWG_STRIPE_SOURCE_READ_ENABLED":"true","OWG_STRIPE_ACCOUNT_ID":account,
         "OWG_STRIPE_RESTRICTED_READ_KEY":key}
    def reader(url,credential):
        assert credential==key
        if url.endswith("/v1/account"):return {"object":"account","id":account}
        return {"id":native,"object":"invoice","status":"paid","paid":True,
                "amount_paid":amount,"currency":currency,"livemode":livemode,
                "customer_email":"private@example.test","total":987654321}
    kwargs={"environment":env,"resolver":lambda ref:RECORD,"reader":reader}
    ordinary=stripe_record_receipt(REF,**kwargs)
    assert "invoice_finance" not in ordinary and ordinary["names_and_amounts_included"] is False
    actual=stripe_record_receipt(REF,include_invoice_amounts=True,**kwargs)
    assert ("invoice_finance" in actual)==valid
    if valid:assert actual["invoice_finance"]=={"amount_paid_minor_units":"123","currency":"USD","live_record_reported":True}
    for secret in (native,account,key,"private@example.test","987654321"):
        assert secret not in str(actual)


@pytest.fixture
def bound_finance(local_db,monitor,monkeypatch):
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    monkeypatch.setattr(source,"ensure_api_token",lambda:"local-test-secret")
    monkeypatch.setattr(source,"resolve_resource_reference",lambda ref:dict(RECORD))
    install_record_events(monkeypatch,RECORD)
    slot=plan["windows"][0]["plan"]["slots"][0]
    view=source.bind("process_1",1,bind_doc(view,slot))
    monkeypatch.setattr(source.stripe_source_evidence,"stripe_record_receipt",lambda *a,**k:receipt())
    view=source.check("process_1",1,{**pins(view),"slot_ids":[slot["slot_id"]],"confirm_source_read":True})
    view=finance.record("process_1",1,finance_request(view))
    return view,slot


def request(view,slot,amount=100):
    return {**pins(view),"expected_reconciliation_sha256":view["finance_reconciliations"][0]["reconciliation_sha256"],
        "confirm_source_amount_read":True,"confirm_gross_invoice_amount_not_booked_revenue":True,
        "rows":[{"slot_id":slot["slot_id"],"declared_amount_paid_minor_units":amount}]}


def test_unknown_retry_resolves_without_rewriting_booked_statement(bound_finance,monkeypatch):
    view,slot=bound_finance
    calls=[]
    def reader(ref,**kwargs):
        calls.append(ref)
        assert kwargs["include_invoice_amounts"] is True
        return money("120",view["latest_report"]["currency"],live=len(calls)>1)
    monkeypatch.setattr(source.stripe_source_evidence,"stripe_record_receipt",reader)
    first=finance_source.check("process_1",1,request(view,slot))
    assert first["source_finance"]["unknown"]==1
    with pytest.raises(ValueError,match="immutable"):
        finance_source.check("process_1",1,request(first,slot,120))
    assert len(calls)==1
    second=finance_source.check("process_1",1,request(first,slot))
    assert second["source_finance"]["mismatched"]==1
    assert second["source_finance"]["selected"]==1
    assert second["source_finance"]["groups"][0]["known_difference_minor_units"]=="20"
    assert second["finance_reconciliations"]==view["finance_reconciliations"]
    assert second["source_finance_checks"][0]==first["source_finance_checks"][0]
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==second
    with pytest.raises(ValueError,match="immutable"):
        finance_source.check("process_1",1,request(second,slot))
    assert len(calls)==2


def test_invalid_pins_and_unconfirmed_read_fail_before_provider(bound_finance,monkeypatch):
    view,slot=bound_finance
    monkeypatch.setattr(source.stripe_source_evidence,"stripe_record_receipt",
        lambda *a,**k:pytest.fail("unexpected provider read"))
    for field,value in (("expected_reconciliation_sha256","f"*64),
        ("expected_head_sha256","f"*64),("confirm_source_amount_read",False)):
        with pytest.raises(ValueError):
            finance_source.check("process_1",1,{**request(view,slot),field:value})
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==view
