"""Cross-workflow overview: exact bounded costs and conservative cohort identity."""
from copy import deepcopy
from fractions import Fraction
import json

import pytest

from server import agent_procurement_workforce_overview as overview
from server import agent_procurement_monitor_journal as journal
from shared.agent_procurement import digest
from shared.agent_procurement_router_rehearsal import _money_bounds
from shared.agent_procurement_portfolio import _pair
from test_agent_procurement_monitor_journal import local_db, doc, monitor, pilot, version_change, rehearsal, trial, study


def _record(key,*,currency="USD",case="a",source="s",upper=20,critical=False,cut=100,revision=1):
    cost=_money_bounds((Fraction(10),None if upper is None else Fraction(upper)))
    arm={"cases":2,"recorded":1,"terminal":1,"counts":{k:{"lower":0,"upper":1} for k in overview.METRICS},
         "attribution_counts":{"pass":1,"fail":0,"unknown":1},"variable_cost":cost}
    state={"plan_sha256":digest(key),"snapshot":{"windows":[{"observations":[{"source_evidence_sha256":digest(source)}]}]},
           "monitor_plan":{"request":{"maximum_reporting_delay_ms":10},
                           "windows":[{"plan":{"slots":[{"case_token_sha256":digest(case)}]}}]},
           "report":{"currency":currency,"as_of_ms":cut,"scheduled_cases":4,"started_window_cases":4,
                     "recorded_cases":2,"arms":{a:deepcopy(arm) for a in overview.ARMS},
                     "windows":[{"window_id":"w1","end_exclusive_ms":150,"state":"hold"}],
                     "recommended_review":"hold","eligible_for_continued_monitoring_review":False},
           "sticky_rollback_triggers":[{"window_id":"w1","reason":"critical"}] if critical else [],
           "rollback_referral_recorded":False,"overdue_window_history":[],"producer_attribution":{"slot":{}}}
    history=[{"kind":"checkpoint","entry_sha256":digest([key,1]),"recorded_at_ms":cut}]
    return key,state,history


def current(*keys):
    return {k[0]:{"revision":k[1],"title":k[0]} for k in keys}


def test_exact_costs_unknown_upper_and_currency_separation():
    a,b,c=("a",1,"m"),("b",1,"m"),("c",1,"m")
    rows=[_record(a,case="a",source="a"),_record(b,case="b",source="b",upper=None),_record(c,case="c",source="c",currency="SEK")]
    r=overview.summarize(rows,current(a,b,c),200)
    assert r["included_journals"]==3
    assert [g["currency"] for g in r["currency_totals"]]==["SEK","USD"]
    usd=r["currency_totals"][1]
    assert usd["recorded_cases"]==4 and usd["scheduled_cases"]==8
    assert _pair(usd["arms"]["incumbent"]["variable_cost"])==(20,None)
    assert _pair(usd["arms"]["incumbent"]["mean_variable_cost"])==(5,None)
    assert usd["credential_authenticated_observations"]==2
    assert not r["realized_savings_calculated"]


def test_overlap_is_symmetric_even_across_revisions_and_currency():
    a,b,c,d=("a",1,"m"),("a",2,"m"),("c",1,"m"),("d",1,"m")
    rows=[_record(a,case="same",source="a",critical=True),_record(b,case="same",source="b",currency="SEK"),
          _record(c,case="unique",source="b"),_record(d,case="d",source="d")]
    r=overview.summarize(rows,current(b,c,d),200)
    assert r["included_journals"]==1 and r["excluded_journals"]==3
    assert r["journals"][0]["priority"]=="rollback_review"
    by={(x["workflow_id"],x["revision"]):x for x in r["journals"]}
    assert "historical_process_revision" in by[("a",1)]["exclusion_reasons"]
    assert "case_token_overlap" in by[("a",2)]["exclusion_reasons"]
    assert "source_reference_overlap" in by[("c",1)]["exclusion_reasons"]
    assert r["totals_may_be_incomplete"]


def test_deadline_and_future_cutoff_and_unreadable():
    a,b=("a",1,"m"),("b",1,"m")
    rows=[_record(a,case="a",source="a"),_record(b,case="b",source="b",cut=201)]
    r=overview.summarize(rows,current(a,b),200,[("broken",1,"m")])
    assert r["journal_count"]==3 and r["included_journals"]==1
    assert r["unreadable_journals"]==[{"workflow_id":"broken","revision":1,"monitor_id":"m"}]
    assert r["journals"][0]["due_for_review_windows"]==["w1"]
    assert r["journals"][0]["clock_refresh_needed"]
    assert "future_snapshot_cutoff" in r["journals"][1]["exclusion_reasons"]


def test_empty_and_legacy_cohort_excluded():
    r=overview.summarize([],{},200)
    assert r["journal_count"]==0 and r["currency_totals"]==[]
    key=("a",1,"m");rec=_record(key)
    rec[1].pop("monitor_plan")
    r=overview.summarize([rec],current(key),200)
    assert r["included_journals"]==0
    assert r["journals"][0]["exclusion_reasons"]==["unavailable_cohort_identity"]


def test_real_sqlite_overview_read_only_and_corruption_visible(local_db,monitor,monkeypatch):
    packet,initial,plan,final=monitor
    with local_db() as conn:
        conn.execute("UPDATE workflow_knowledge SET payload=?",(json.dumps({"title":"Reviewed workflow","process_model":{"goal":"test"}}),))
    head=journal.append(packet,"process_1",1,doc(initial,plan,final))
    monkeypatch.setattr(overview.time,"time",lambda:5)
    result=overview.overview()
    assert result["included_journals"]==1 and result["journal_count"]==1
    assert result["journals"][0]["title"]=="Reviewed workflow"
    assert result["journals"][0]["head_sha256"]==head["head_sha256"]
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==head
    with local_db() as conn:
        conn.execute("UPDATE procurement_monitor_journal_entries SET entry_json='{}'")
    result=overview.overview()
    assert result["readable_journals"]==0 and result["totals_may_be_incomplete"]
    assert result["unreadable_journals"][0]["monitor_id"]=="monitor-1"
