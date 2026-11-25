"""Bounded multi-cutoff simulation checks."""
import pytest
from server import event_log_simulation_temporal as temporal
from server import event_log_workspace as workspace
from test_event_log_simulation_validation import _data_dir,_log


def settings(**changes):
    return {"holdout_shares":[.2,.4],"seeds":[1,2],"cases":100,
            "distribution":"fixed","max_absolute_relative_error":.01,**changes}


def test_stable_process_multiple_splits():
    r=temporal.review(_log(cases=200),settings())
    assert r["run_count"]==4 and r["distinct_cutoffs"]==2
    assert r["all_metrics_within_all_tested"]
    assert not r["forecast_or_guarantee"] and not r["independent_holdouts_verified"]
    assert {(x["holdout_share"],x["seed"]) for x in r["runs"]}=={(.2,1),(.2,2),(.4,1),(.4,2)}


def test_changed_process_flags_miss():
    r=temporal.review(_log(cases=300,change_at=240),settings())
    assert not r["all_metrics_within_all_tested"]
    assert r["metric_summary"]["p50"]["outside"]>0


@pytest.mark.parametrize("patch",[{"holdout_shares":[.2,.2]},{"holdout_shares":[.01,.2]},
    {"seeds":[1,1]},{"seeds":[True]},{"cases":True},{"cases":2001},
    {"max_absolute_relative_error":-1},{"extra":True}])
def test_invalid_settings(patch):
    with pytest.raises(workspace.EventLogWorkspaceError):
        temporal.review("unused",settings(**patch))


def test_budget_refuses(monkeypatch):
    monkeypatch.setattr(temporal,"MAX_PROJECTED_EVENTS",1)
    with pytest.raises(workspace.EventLogWorkspaceError,match="budget"):
        temporal.review(_log(cases=30),settings())
