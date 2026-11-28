"""Declared finance ledger arithmetic, exact report pins, and persistence."""
from copy import deepcopy
from fractions import Fraction
from itertools import product

import pytest

from server import agent_procurement_finance as finance
from server import agent_procurement_monitor_journal as journal
from shared.agent_procurement import digest
from shared.agent_procurement_portfolio import _pair,_read
from test_agent_procurement_monitor_journal import local_db,doc,monitor,pilot,version_change,rehearsal,trial,study


def request(view,window="w1"):
    start=2000 if window=="w1" else 3000
    return {"monitor_id":view["monitor_id"],"window_id":window,
        "expected_head_sha256":view["head_sha256"],"expected_report_sha256":view["latest_report"]["report_sha256"],
        "currency":view["latest_report"]["currency"],"reviewer_alias":"finance-reviewer","accounting_basis":"accrual",
        "period_start_ms":start,"period_end_exclusive_ms":start+1000,
        "booked":[{"arm":a,"category":c,"amount_microunits":1200 if c=="revenue" else 100 if a=="incumbent" else 60,
                   "source_reference_sha256":digest(["finance",window,a,c])}
                   for a,c in product(finance.ARMS,(*finance.COSTS,"revenue"))],
        "confirm_complete_period_bookings":True,"confirm_disjoint_arm_and_category_accounting":True,
        "confirm_exclude_imputed_labor_from_booked_cost":True,"confirmed":True}


def test_booked_amounts_separate_from_telemetry_and_retained(local_db,monitor):
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    req=request(view)
    result=finance.record("process_1",1,req)
    f=result["finance_reconciliations"][0]
    assert f["accounting_basis"]=="accrual"
    assert f["arms"]["incumbent"]["declared_booked_cost_microunits"]=="500"
    assert f["arms"]["challenger"]["declared_booked_cost_microunits"]=="300"
    assert _read(f["challenger_minus_incumbent_booked_net_per_case"])==Fraction(100,3)
    assert _pair(f["arms"]["incumbent"]["telemetry_direct_cost"])==(600,600)
    assert _pair(f["arms"]["incumbent"]["booked_provider_external_minus_telemetry_direct"])==(-400,-400)
    assert not f["realized_roi_verified"] and not f["source_financial_documents_verified"]
    assert result["history"][-1]["kind"]=="finance_reconciliation"
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==result
    snapshot["as_of_ms"]+=1
    later=journal.append(packet,"process_1",1,doc(initial,plan,snapshot,result["head_sha256"]))
    assert later["finance_reconciliations"]==[f]
    assert later["latest_report"]["report_sha256"]!=f["monitor_report_sha256"]


def test_missing_telemetry_does_not_reduce_booked_cost(local_db,monitor):
    packet,initial,plan,snapshot=monitor
    snapshot["windows"][0]["observations"]=[]
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    result=finance.record("process_1",1,request(view))["finance_reconciliations"][0]
    for a in finance.ARMS:
        f=result["arms"][a]
        assert f["scheduled_cases"]==6 and f["recorded_cases"]==0
        assert _pair(f["telemetry_direct_cost"])==(0,None)
        assert _pair(f["booked_provider_external_minus_telemetry_direct"])[0] is None
        assert not f["telemetry_complete"]
        assert int(f["declared_booked_cost_microunits"])>0


def test_duplicate_window_and_reused_source_fail_closed(local_db,monitor):
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    a=finance.record("process_1",1,request(view))
    with pytest.raises(journal.JournalError,match="already reconciled"):
        finance.record("process_1",1,request(a))
    req=request(a,"w2")
    req["booked"][0]["source_reference_sha256"]=a["finance_reconciliations"][0]["source_reference_sha256"][0]
    with pytest.raises(ValueError,match="reference reused"):
        finance.record("process_1",1,req)
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==a
    b=finance.record("process_1",1,request(a,"w2"))
    assert len(b["finance_reconciliations"])==2


@pytest.mark.parametrize("mutate",["head","report","currency","period","reviewer","confirm","missing","duplicate","negative","future"])
def test_invalid_finance_statement_no_write(local_db,monitor,monkeypatch,mutate):
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    req=request(view)
    if mutate=="head":req["expected_head_sha256"]="f"*64
    elif mutate=="report":req["expected_report_sha256"]="f"*64
    elif mutate=="currency":req["currency"]="ZZZ"
    elif mutate=="period":req["period_start_ms"]+=1
    elif mutate=="reviewer":req["reviewer_alias"]="MONITOR-OWNER"
    elif mutate=="confirm":req["confirm_complete_period_bookings"]=False
    elif mutate=="missing":req["booked"].pop()
    elif mutate=="duplicate":req["booked"][1]["source_reference_sha256"]=req["booked"][0]["source_reference_sha256"]
    elif mutate=="negative":req["booked"][0]["amount_microunits"]=-1
    elif mutate=="future":monkeypatch.setattr(finance.time,"time",lambda:2.9)
    with pytest.raises(ValueError):finance.record("process_1",1,req)
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==view


def test_exact_finance_arithmetic_over_unequal_populations():
    # Standalone oracle: no telemetry or reviewer pins needed to test pure math.
    from types import SimpleNamespace
    for n,m,amount in product(range(1,5),range(1,5),range(4)):
        rows=[SimpleNamespace(arm=a,category=c,amount_microunits=amount if a=="challenger" else 1)
              for a,c in product(finance.ARMS,(*finance.COSTS,"revenue"))]
        window={"plan":{"slots":[{"slot_id":str(i),"arm":"incumbent" if i<n else "challenger"} for i in range(n+m)]}}
        arms,delta=finance._calculate(SimpleNamespace(booked=rows),window,[])
        assert _read(delta)==Fraction(-4*amount,m)+Fraction(4,n)
        assert _pair(arms["incumbent"]["telemetry_direct_cost"])==(0,None)
        assert _pair(arms["challenger"]["booked_provider_external_minus_telemetry_direct"])==(None,2*amount)
