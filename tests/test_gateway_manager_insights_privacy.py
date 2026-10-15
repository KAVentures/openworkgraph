"""Manager visibility is strictly aggregate and redacted, never a people tracker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from gateway import manager_insights as module


def events(people=3, *, private_title="PRIVATE_CUSTOMER_123"):
    base = datetime(2026, 10, 8, tzinfo=timezone.utc)
    data = []
    for p in range(people):
        for day in range(2):
            for seq, app in enumerate(("Gmail", "Microsoft Excel", "Fortnox", "Gmail")):
                at=base+timedelta(days=day, hours=p, minutes=seq)
                data.append({
                    "event_id":f"private-{p}-{day}-{seq}",
                    "observed_at":at.isoformat(),
                    "organization_id":"org-a",
                    "actor_id":f"person-private-{p}",
                    "device_id":f"endpoint-private-{p}",
                    "session_id":f"session-private-{p}-{day}",
                    "app":app,
                    "window_title":private_title,
                    "metadata":{"message_body":"LEAK_THIS_IF_BUG"},
                    "duration_seconds":100,
                })
    return data


def test_manager_insights_suppresses_small_teams(monkeypatch):
    monkeypatch.setattr(module,"load_gateway_evidence",lambda db,**kw: events(2))
    report=module.manager_insights(None,organization_id="org-a")
    assert report["workflows"] == []
    assert report["overview"]["visible_patterns"] == 0
    assert "person-private-" not in str(report)


def test_three_people_reveal_only_high_level_ordered_work(monkeypatch):
    recorded=[]
    def load(db,**kw):
        recorded.append(kw)
        return events(3)
    monkeypatch.setattr(module,"load_gateway_evidence",load)
    report=module.manager_insights(None,organization_id="org-a",min_contributors=1)
    assert recorded==[{
        "organization_id":"org-a","actor_id":None,
        "since":None,"until":None,"limit":25000
    }]
    assert report["overview"]["minimum_contributors"] == 3
    assert report["workflows"] and len(report["workflows"])==1
    item=report["workflows"][0]
    assert item["apps"] == ["Gmail","Excel","Fortnox","Gmail"]
    assert item["contributing_people"] == 3
    assert item["observed_sessions"] == 6
    assert item["automation_candidate"] is True
    assert item["observed_duration_hours"] > 0
    for sensitive in ("PRIVATE_CUSTOMER_123","LEAK_THIS_IF_BUG","person-private-",
                      "endpoint-private-","session-private-","private-0-"):
        assert sensitive not in str(report)
    assert report["privacy"]["never_claim_automation_savings"]


def test_no_raw_unknown_app_name_or_employee_performance(monkeypatch):
    sample=events(3)
    for item in sample:
        item["app"]="Customer Alice Confidential"
    monkeypatch.setattr(module,"load_gateway_evidence",lambda db,**kw:sample)
    report=module.manager_insights(None,organization_id="org-a")
    assert "Alice" not in str(report)
    assert report["workflows"]==[]


def test_source_row_budget_and_actor_consents_are_not_inferred(monkeypatch):
    sample=events(3)
    for item in sample:
        item["actor_id"]=""  # unknown identity cannot count toward threshold
    monkeypatch.setattr(module,"load_gateway_evidence",lambda db,**kw:sample)
    result=module.manager_insights(None,organization_id="org-a",event_limit=1)
    assert result["workflows"]==[]
    assert result["overview"]["source_row_limit"]==100
    assert result["overview"]["no_pattern_does_not_mean_no_repeated_work"] is True
