"""Root cause tests numeric case attributes by quartile range."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from server import event_log_analytics as analytics
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


def _log(orders=40, distinct=True, numeric_base=100, region_name="region"):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    objects, events = [], []
    for i in range(orders):
        amount = (numeric_base + i * 37) if distinct else (100 if i % 2 else 200)
        objects.append({"id": f"o{i}", "type": "order",
                        "attributes": [{"name": "amount", "time": _iso(start), "value": amount},
                                       {"name": region_name, "time": _iso(start), "value": "EU" if i % 2 else "US"}]})
        t = start + timedelta(days=i)
        # The largest quarter of orders takes ten days; the rest take one.
        took = timedelta(days=10 if i >= orders * 3 // 4 else 1)
        for k, (activity, at) in enumerate([("Create", t), ("Ship", t + took)]):
            events.append({"id": f"e{i}-{k}", "type": activity, "time": _iso(at), "attributes": [],
                           "relationships": [{"objectId": f"o{i}", "qualifier": ""}]})
    doc = {"objectTypes": [{"name": "order", "attributes": []}],
           "eventTypes": [{"name": n, "attributes": []} for n in ("Create", "Ship")], "objects": objects, "events": events}
    return workspace.import_log(BytesIO(json.dumps(doc).encode()), name="o", source_format="ocel")["log_id"]


def test_numeric_attribute_quartiles_are_tested():
    log = _log()
    r = analytics.root_cause(log, "duration_over", "order", threshold_seconds=5 * 86400)
    ranges = {a["feature"]["value"]: a for a in r["associations"] if a["feature"]["kind"] == "attribute_range"}
    top = next(v for k, v in ranges.items() if "amount ≥" in k)
    assert top["cases_with_feature"] == 10 and top["outcome_rate_with"] == 1.0 and top["outcome_rate_without"] == 0.0
    assert top["holds_after_bonferroni"]
    assert any(k.startswith("amount <") for k in ranges) and any("≤ amount <" in k for k in ranges)
    # The first association is the top quartile; exact amounts are too rare to be tested on their own.
    assert r["associations"][0]["feature"] == top["feature"]
    assert not any(a["feature"]["value"].startswith("amount=") for a in r["associations"])
    assert "quartile" in r["numeric_attributes"]
    # Text attributes are not ranged.
    assert not any("region" in k for k in ranges)


def test_numbers_with_few_values_are_not_ranged():
    log = _log(distinct=False)
    r = analytics.root_cause(log, "duration_over", "order", threshold_seconds=5 * 86400)
    assert not [a for a in r["associations"] if a["feature"]["kind"] == "attribute_range"]


def _cases(log, cohort):
    from server import event_log_cohorts as cohorts

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(cohort), log)
        return {"cases": conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]}
    return workspace._query(log, "order", run)["cases"]


def test_each_association_carries_a_filter_selecting_exactly_its_cases():
    log = _log()
    r = analytics.root_cause(log, "duration_over", "order", threshold_seconds=5 * 86400)
    hinted = [a for a in r["associations"] if "cohort" in a]
    assert {a["feature"]["kind"] for a in hinted} >= {"attribute_range", "attribute"}
    for a in hinted:
        assert _cases(log, a["cohort"]) == a["cases_with_feature"], a


def test_attribute_range_filter_validation():
    from server import event_log_cohorts as cohorts
    log = _log()
    assert _cases(log, {"attribute_range": {"name": "amount", "min": 1000}}) == sum(1 for i in range(40) if 100 + i * 37 >= 1000)
    assert _cases(log, {"attribute_range": {"name": "amount", "max": 137}}) == 1
    assert _cases(log, {"attribute_range": {"name": "region", "min": 0}}) == 0  # text never matches a range
    for bad in ({"name": "amount"}, {"name": "amount", "min": 5, "max": 5}, {"name": "", "min": 1},
                {"name": "amount", "min": "5"}, {"name": "amount", "min": True}, {"name": "amount", "min": float("nan")},
                {"name": "amount", "min": 1, "extra": 2}, ["amount"]):
        with pytest.raises(workspace.EventLogWorkspaceError):
            cohorts.parse_cohort({"attribute_range": bad})
    assert cohorts.describe(cohorts.parse_cohort({"attribute_range": {"name": "amount", "min": 2}})) == \
        {"attribute_range": {"name": "amount", "min": 2.0}}


def test_route_accepts_attribute_range(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = {"objectTypes": [{"name": "order", "attributes": []}], "eventTypes": [{"name": "A", "attributes": []}],
       "objects": [{"id": f"o{i}", "type": "order", "attributes": [{"name": "amount", "time": "2024-01-01T00:00:00Z", "value": i}]} for i in range(5)],
       "events": [{"id": f"e{i}", "type": "A", "time": "2024-01-01T00:00:00Z", "attributes": [],
                   "relationships": [{"objectId": f"o{i}", "qualifier": ""}]} for i in range(5)]}
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    log_id = client.post("/v1/event-logs/import?format=ocel&name=t", content=json.dumps(doc).encode(),
                         headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    q = json.dumps({"name": "amount", "min": 2, "max": 4})
    r = client.get(f"/v1/event-logs/{log_id}/summary", params={"case_type": "order", "attribute_range": q}, headers=owner)
    assert r.status_code == 200 and r.json()["cases"] == 2, r.text
    assert client.get(f"/v1/event-logs/{log_id}/summary", params={"case_type": "order", "attribute_range": "{"}, headers=owner).status_code == 422
    assert client.get(f"/v1/event-logs/{log_id}/summary", params={"case_type": "order", "attribute_range": '{"name":"amount"}'}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


def test_close_quartile_edges_select_their_exact_cases():
    # Six significant digits collapse the two middle labels to the same text.
    log = _log(numeric_base=1_000_000_000)
    result = analytics.root_cause(log, "duration_over", "order", threshold_seconds=5 * 86400)
    ranges = [a for a in result["associations"] if a["feature"]["kind"] == "attribute_range"]
    assert len(ranges) == 4
    assert len({a["feature"]["value"] for a in ranges}) == 4
    for a in ranges:
        assert _cases(log, a["cohort"]) == a["cases_with_feature"] == 10


def test_attribute_name_with_equals_does_not_offer_an_inexact_filter():
    log = _log(region_name="region=code")
    result = analytics.root_cause(log, "duration_over", "order", threshold_seconds=5 * 86400)
    features = [a for a in result["associations"] if a["feature"]["kind"] == "attribute" and a["feature"]["value"].startswith("region=code=")]
    assert features
    assert all("cohort" not in a for a in features)
