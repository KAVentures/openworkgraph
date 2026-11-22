"""Model change: discovered models before and after a split date."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_cases as case_pages
from server import event_log_model_change as mc
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    """Until March: Order, Check, Approve, Ship. From March: Order, Approve, Check, Ship, and Notify is new."""
    rows = ["case,activity,time"]
    for i in range(20):
        for j, a in enumerate(["Order", "Check", "Approve", "Ship"]):
            rows.append(f"a{i},{a},2024-01-{1 + i:02d}T0{j}:00:00Z")
    for i in range(20):
        for j, a in enumerate(["Order", "Approve", "Check", "Ship", "Notify"]):
            rows.append(f"b{i},{a},2024-03-{1 + i:02d}T0{j}:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="c", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_orderings_activities_and_cross_fit_change():
    log = _log()
    r = mc.model_change(log, split="2024-02-15", noise=0)
    assert r["periods"]["before"]["cases"] == 20 and r["periods"]["after"]["cases"] == 20
    assert r["activities"]["only_after"] == ["Notify"] and r["activities"]["only_before"] == []
    changed = {(c["from"], c["to"]): (c["before"], c["after"]) for c in r["changes"]}
    assert changed[("Approve", "Check")] == ("←", "→")
    cross = r["cross_fit"]
    assert cross["before_cases_on_before_model"]["fitness"] == 1.0
    assert cross["after_cases_on_after_model"]["fitness"] == 1.0
    assert cross["after_cases_on_before_model"]["fitness"] < 1.0
    assert r["fitness_drop_on_earlier_model"] > 0


def test_dashboard_cross_fit_variants_match_exact_scoped_cases():
    log = _log()
    scope = {"activity": "Order"}
    aggregate = mc.model_change(log, split="2024-02-15", noise=0, cohort=scope)
    assert "case_selections" not in aggregate
    assert "non_fitting_variants" not in aggregate["cross_fit"]["after_cases_on_before_model"]
    report = mc.model_change(log, split="2024-02-15", noise=0, cohort=scope, include_case_evidence=True)
    fit = report["cross_fit"]["after_cases_on_before_model"]
    assert fit["cases"] == fit["cases_total"] == 20
    assert fit["cases_excluded"] == fit["cases_unaligned"] == 0
    assert fit["non_fitting_variants_total"] == 1
    variant, = fit["non_fitting_variants"]
    assert variant["path"] == ["Order", "Approve", "Check", "Ship", "Notify"]
    assert variant["fitness"] < 1 and variant["cost"] > 0
    assert report["case_selections"]["after"] == {**scope, "started_from": "2024-02-15T00:00:00Z"}
    evidence = case_pages.cases(log, {"cohort": report["case_selections"]["after"], "path": variant["path"]})
    assert evidence["selected_cases"] == variant["cases"]
    assert {case["case_id"] for case in evidence["cases"]} == {f"b{i}" for i in range(20)}


def test_median_split_and_refusals():
    log = _log()
    r = mc.model_change(log)
    assert r["split_chosen"] == "median case start" and r["split"].startswith("2024-03-01")
    with pytest.raises(EventLogWorkspaceError, match="each period needs"):
        mc.model_change(log, split="2024-01-05")
    with pytest.raises(EventLogWorkspaceError, match="split must be"):
        mc.model_change(log, split="soon")
    with pytest.raises(EventLogWorkspaceError, match="start-date filter"):
        mc.model_change(log, split="2024-02-15", cohort={"started_from": "2024-01-01T00:00:00Z"})


def test_two_groups_by_case_attribute(tmp_path):
    from server import event_log_case_attributes as case_attributes
    log = _log()
    table = tmp_path / "regions.csv"
    table.write_text("id,region\n" + "".join(f"a{i},EU\n" for i in range(20)) + "".join(f"b{i},US\n" for i in range(20)))
    case_attributes.add(log, table, id_column="id", columns=["region"])
    r = mc.model_comparison(log, "region=EU", "region=US", noise=0)
    assert r["groups"]["a"] == {"filter": "region=EU", "cases": 20, "matched_by": "case_attribute"}
    assert r["groups"]["b"]["cases"] == 20
    assert r["activities"]["only_b"] == ["Notify"]
    diff = {(d["from"], d["to"]): (d["a"], d["b"]) for d in r["differences"]}
    assert diff[("Approve", "Check")] == ("←", "→")
    assert r["cross_fit"]["a_cases_on_a_model"]["fitness"] == 1.0 and r["fitness_gap_on_a_model"] > 0
    with pytest.raises(EventLogWorkspaceError, match="attribute=value"):
        mc.model_comparison(log, "EU", "region=US")
    with pytest.raises(EventLogWorkspaceError, match="two different"):
        mc.model_comparison(log, "region=EU", "region=EU")
    with pytest.raises(EventLogWorkspaceError, match="at least 10 cases"):
        mc.model_comparison(log, "region=EU", "region=APAC")
