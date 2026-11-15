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


def _log(orders=40, distinct=True):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    objects, events = [], []
    for i in range(orders):
        amount = (100 + i * 37) if distinct else (100 if i % 2 else 200)
        objects.append({"id": f"o{i}", "type": "order",
                        "attributes": [{"name": "amount", "time": _iso(start), "value": amount},
                                       {"name": "region", "time": _iso(start), "value": "EU" if i % 2 else "US"}]})
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
