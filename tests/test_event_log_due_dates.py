"""Due-date adherence of a chosen activity."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_due_dates as dd
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "attribute_columns": ["due"]}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(rows):
    text = "case,activity,time,due\n" + "\n".join(",".join(r) for r in rows)
    return workspace.import_log(BytesIO(text.encode()), name="d", source_format="csv", mapping=MAPPING)["log_id"]


ROWS = [
    # on time: paid the day it was due (date-only due dates last to the end of the day)
    ("a", "Invoice", "2024-03-01T09:00:00Z", "2024-03-10"), ("a", "Pay", "2024-03-10T17:00:00Z", ""),
    # late by 2.5 days
    ("b", "Invoice", "2024-03-01T09:00:00Z", "2024-03-05"), ("b", "Pay", "2024-03-08T12:00:00Z", ""),
    # early: 20 days before due
    ("c", "Invoice", "2024-03-01T09:00:00Z", "2024-03-25T00:00:00Z"), ("c", "Pay", "2024-03-05T00:00:00Z", ""),
    # open and overdue (due before the log's last event, 2024-03-20)
    ("d", "Invoice", "2024-03-01T09:00:00Z", "2024-03-15"),
    # open, not yet due
    ("e", "Invoice", "2024-03-20T09:00:00Z", "2024-04-30"),
    # no due date, and an unreadable one
    ("f", "Invoice", "2024-03-02T09:00:00Z", ""), ("f", "Pay", "2024-03-03T09:00:00Z", ""),
    ("g", "Invoice", "2024-03-02T09:00:00Z", "soon"), ("g", "Pay", "2024-03-03T09:00:00Z", ""),
]


def test_groups_from_a_due_date_attribute():
    log = _import(ROWS)
    r = dd.due_dates(log, "Pay", due_attribute="due", early_days=7)
    assert r["groups"] == {"on_time": 1, "early": 1, "late": 1, "open_overdue": 1, "open_not_due": 1,
                           "no_due_date": 1, "unreadable_due_date": 1}
    assert r["evaluated"] == 5 and r["on_time_share_of_done"] == round(1 / 3, 4)
    assert r["days_late"]["median"] == 2.5 and r["most_late_cases"][0]["case_id"] == "b"
    march = next(m for m in r["by_due_month"] if m["due_month"] == "2024-03")
    assert (march["on_time"], march["late"], march["early"], march["open_overdue"]) == (1, 1, 1, 1)
    # Without an early window, paying early counts as on time.
    assert dd.due_dates(log, "Pay", due_attribute="due")["groups"]["on_time"] == 2


def test_due_case_pages_share_summary_classification_and_keep_literal_keys():
    log = _import(ROWS + [(" b literal ", "Invoice", "2024-03-01T09:00:00Z", "2024-03-05"),
                          (" b literal ", "Pay", "2024-03-08T12:00:00Z", ""),
                          ("overflow", "Invoice", "2024-03-01T09:00:00Z", "9999-12-31")])
    options = {"due_attribute": "due", "early_days": 7}
    summary = dd.due_dates(log, "Pay", **options)
    for group in dd.GROUPS:
        page = dd.due_cases(log, "Pay", group=group, **options)
        assert page["total"] == len(page["cases"]) == summary["groups"][group]
    first = dd.due_cases(log, "Pay", group="late", limit=1, **options)
    assert first["next_after"] == " b literal "
    assert first["cases"][0]["days_from_due"] == 2.5
    second = dd.due_cases(log, "Pay", group="late", after=first["next_after"], limit=1, **options)
    assert second["cases"][0]["case_id"] == "b" and second["next_after"] is None
    overdue = dd.due_cases(log, "Pay", group="open_overdue", **options)["cases"]
    assert overdue == [{"case_id": "d", "done_at": None, "due_at": "2024-03-16T00:00:00Z", "days_from_due": None}]


@pytest.mark.parametrize("options", [{"group": "unknown"}, {"limit": True}, {"after": ""}, {"limit": 101}])
def test_due_case_pages_reject_invalid_queries(options):
    with pytest.raises(EventLogWorkspaceError):
        dd.due_cases("not-read", "Pay", **{"group": "late", "due_days": 1, **options})


def test_fixed_days_after_start_and_withheld_ids():
    log = _import(ROWS)
    r = dd.due_dates(log, "Pay", due_days=5, include_case_ids=False)
    # a: 9.3 days → late; b: 7.1 → late; c: 3.6 → on time; f, g: 1 day → on time; d overdue; e not yet due.
    assert r["groups"]["late"] == 2 and r["groups"]["on_time"] == 3
    assert r["groups"]["open_overdue"] == 1 and r["groups"]["open_not_due"] == 1
    assert r["groups"]["no_due_date"] == 0 and r["most_late_cases"] is None


def test_all_open_cohort_uses_the_case_notions_observation_end():
    log = _import(ROWS + [("marker", "Pay", "2024-05-02T00:00:00Z", "")])
    options = {"due_attribute": "due", "cohort": {"without_activity": "Pay"}}
    summary = dd.due_dates(log, "Pay", **options)
    assert summary["groups"]["open_overdue"] == 2
    page = dd.due_cases(log, "Pay", group="open_overdue", **options)
    assert page["log_end"] == summary["log_end"] == "2024-05-02T00:00:00Z"
    assert [row["case_id"] for row in page["cases"]] == ["d", "e"]


def test_as_of_due_dates_and_case_pages_use_events_visible_at_the_cutoff():
    log = _import(ROWS + [("future-terms", "Invoice", "2024-03-02T10:00:00Z", ""),
                          ("future-terms", "Terms recorded", "2024-03-09T10:00:00Z", "2024-03-05")])
    cutoff = "2024-03-07T02:00:00+02:00"
    options = {"due_attribute": "due", "as_of": cutoff, "cohort": {"without_activity": "Pay"}}
    summary = dd.due_dates(log, "Pay", **options)
    assert summary["as_of"] == "2024-03-07T00:00:00Z"
    assert summary["log_end"] == "2024-03-20T09:00:00Z"
    assert summary["cases"] == 4 and summary["groups"]["no_due_date"] == 1
    assert summary["groups"]["open_overdue"] == 1 and summary["groups"]["open_not_due"] == 2
    assert summary["deadline_attribute_basis"] == "events_at_or_before_cutoff"
    overdue = dd.due_cases(log, "Pay", group="open_overdue", **options)
    assert overdue["as_of"] == summary["as_of"]
    assert overdue["cases"] == [{"case_id": "b", "done_at": None, "due_at": "2024-03-06T00:00:00Z", "days_from_due": None}]
    at_completion = dd.due_cases(log, "Pay", group="late", due_attribute="due", as_of="2024-03-08T12:00:00Z")
    assert [item["case_id"] for item in at_completion["cases"]] == ["b"]
    earlier = dd.due_dates(log, "Pay", due_days=0.1, as_of="2024-03-01T20:00:00Z")
    assert earlier["cases"] == earlier["groups"]["open_overdue"] == 4


def test_bad_requests():
    log = _import(ROWS)
    with pytest.raises(EventLogWorkspaceError, match="either"):
        dd.due_dates(log, "Pay")
    with pytest.raises(EventLogWorkspaceError, match="either"):
        dd.due_dates(log, "Pay", due_attribute="due", due_days=3)
    with pytest.raises(EventLogWorkspaceError, match="no case"):
        dd.due_dates(log, "Ship", due_days=3)
    with pytest.raises(EventLogWorkspaceError, match="due_days"):
        dd.due_dates(log, "Pay", due_days=0)
    for invalid in ("2024-03-10", "2024-03-10T12:00:00", "0001-01-01T00:00:00+02:00"):
        with pytest.raises(EventLogWorkspaceError, match="timezone-qualified"):
            dd.due_dates(log, "Pay", due_days=1, as_of=invalid)


def test_a_joined_case_attribute_wins_over_the_event_attribute(tmp_path):
    from server import event_log_case_attributes as case_attributes
    log = _import(ROWS)
    table = tmp_path / "terms.csv"
    table.write_text("id,payment_due\nb,2024-03-09\n")
    case_attributes.add(log, table, id_column="id", columns=["payment_due"])
    r = dd.due_dates(log, "Pay", due_attribute="payment_due")
    # Only case b has the joined attribute; paid 2024-03-08 → on time. Every other case has no due date.
    assert r["groups"]["on_time"] == 1 and r["groups"]["no_due_date"] == 6
    historical = dd.due_dates(log, "Pay", due_attribute="payment_due", as_of="2024-03-10T17:00:00Z")
    assert historical["evaluated"] == 0 and historical["groups"]["no_due_date"] == historical["cases"]
