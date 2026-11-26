"""Retained additive effort is not elapsed case duration."""
from datetime import date
from io import BytesIO
import json
import pytest

from server import automation_benefits as benefits
from server import automation_pipeline as pipeline
from server import automation_retained_effort as effort
from server import event_log_workspace as workspace

MAPPING={"case_column":"case","activity_column":"activity","timestamp_column":"time",
         "attribute_columns":["effort"]}
HEADER="case,activity,time,effort\n"
ROWS=[
    "a,Start,2024-01-01T00:00:00Z,",
    "a,Work,2024-01-01T01:00:00Z,60",
    "a,Work,2024-01-01T02:00:00Z,30",
    "a,Close,2024-01-01T03:00:00Z,",
    "b,Start,2024-01-02T00:00:00Z,",
    "b,Work,2024-01-02T01:00:00Z,30",
    "b,Close,2024-01-02T02:00:00Z,",
    "c,Start,2023-12-31T00:00:00Z,",
    "c,Work,2024-01-01T04:00:00Z,900",
    "c,Close,2024-01-01T05:00:00Z,",
]


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    monkeypatch.setattr(pipeline,"DATA_DIR",tmp_path)


def make(rows=ROWS):
    log=workspace.import_log(BytesIO((HEADER+"\n".join(rows)+"\n").encode()),
                             name="Effort",source_format="csv",mapping=MAPPING)
    idea=pipeline.create({"title":"Effort review"})
    idea=pipeline.transition(idea["idea_id"],{"to":"assessing","expected_revision":idea["revision"]})
    idea=pipeline.assess(idea["idea_id"],{"ease":{q:5 for q in pipeline.EASE_QUESTIONS},
        "cases_per_month":300,"minutes_per_case":10,"eligible_share":0.5,"expected_revision":idea["revision"]})
    idea=pipeline.transition(idea["idea_id"],{"to":"approved","expected_revision":idea["revision"]})
    spec={"expected_revision":idea["revision"],"log_id":log["log_id"],"case_type":"case",
          "period":"baseline","start":"2024-01-01","end":"2024-01-02",
          "completion_activity":"Close","effort_activity":"Work","effort_attribute":"effort",
          "unit":"minutes","effort_semantics_confirmed":True}
    return idea["idea_id"],spec


def test_exact_case_cohort_and_pinned_save():
    idea,spec=make()
    result=effort.preview(idea,spec)
    assert result["recordable"]
    assert result["counts"]["started_cases"]==2
    assert result["counts"]["complete_cases"]==2
    assert result["counts"]["effort_events"]==3
    assert result["total_hours"]==2
    assert result["minutes_per_case"]==60
    assert not benefits.report(idea)["measurements"]
    with pytest.raises(benefits.BenefitError,match="preview"):
        effort.record(idea,spec)
    with pytest.raises(benefits.BenefitError,match="changed"):
        effort.record(idea,{**spec,"unit":"seconds","expected_preview":result["preview_sha256"]})
    report=effort.record(idea,{**spec,"expected_preview":result["preview_sha256"]})
    row=report["measurements"][0]
    assert row["total_hours"]==2 and row["cases"]==2
    assert row["source"]=="event_log"
    assert row["source_reference"]==result["source_reference"]
    assert json.loads(row["note"])["settings"]["effort_attribute"]=="effort"
    with pytest.raises(benefits.BenefitError,match="already recorded"):
        effort.record(idea,{**spec,"expected_preview":result["preview_sha256"]})


@pytest.mark.parametrize("replacement,blocker",[
    ("b,Work,2024-01-02T01:00:00Z,","missing_effort_values"),
    ("b,Work,2024-01-02T01:00:00Z,-5","invalid_effort_values"),
    ("b,Work,2024-01-02T01:00:00Z,NaN","invalid_effort_values"),
    ("b,Other,2024-01-02T01:00:00Z,30","cases_without_effort_activity"),
])
def test_coverage_blocks_recording(replacement,blocker):
    rows=list(ROWS);rows[5]=replacement
    idea,spec=make(rows)
    result=effort.preview(idea,spec)
    assert not result["recordable"] and result["total_hours"] is None
    assert blocker in result["blockers"]
    with pytest.raises(benefits.BenefitError,match="blockers"):
        effort.record(idea,{**spec,"expected_preview":result["preview_sha256"]})


def test_cross_boundary_and_overlapping_manual_record():
    rows=list(ROWS);rows[6]="b,Close,2024-01-03T00:00:00Z,"
    idea,spec=make(rows)
    assert "incomplete_or_cross_boundary_cases" in effort.preview(idea,spec)["blockers"]
    idea,spec=make()
    result=effort.preview(idea,spec)
    benefits.record(idea,{"period":"baseline","start":"2024-01-02","end":"2024-01-02","cases":1,"total_hours":1})
    with pytest.raises(benefits.BenefitError,match="overlaps"):
        effort.record(idea,{**spec,"expected_preview":result["preview_sha256"]})


def test_explicit_semantics_and_numeric_parsing():
    idea,spec=make()
    with pytest.raises(benefits.BenefitError,match="additive"):
        effort.preview(idea,{**spec,"effort_semantics_confirmed":False})
    for bad in [True,[],{},"-1","Infinity","1e100000","1e-100000"]:
        assert effort._number(json.dumps({"x":bad}),"x")[1]=="invalid"
    assert effort._number('{"x":0}',"x")[1]=="valid"
    assert effort._number('{}',"x")[1]=="missing"
