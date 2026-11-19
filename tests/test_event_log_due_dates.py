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


def test_fixed_days_after_start_and_withheld_ids():
    log = _import(ROWS)
    r = dd.due_dates(log, "Pay", due_days=5, include_case_ids=False)
    # a: 9.3 days → late; b: 7.1 → late; c: 3.6 → on time; f, g: 1 day → on time; d overdue; e not yet due.
    assert r["groups"]["late"] == 2 and r["groups"]["on_time"] == 3
    assert r["groups"]["open_overdue"] == 1 and r["groups"]["open_not_due"] == 1
    assert r["groups"]["no_due_date"] == 0 and r["most_late_cases"] is None


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


def test_a_joined_case_attribute_wins_over_the_event_attribute(tmp_path):
    from server import event_log_case_attributes as case_attributes
    log = _import(ROWS)
    table = tmp_path / "terms.csv"
    table.write_text("id,payment_due\nb,2024-03-09\n")
    case_attributes.add(log, table, id_column="id", columns=["payment_due"])
    r = dd.due_dates(log, "Pay", due_attribute="payment_due")
    # Only case b has the joined attribute; paid 2024-03-08 → on time. Every other case has no due date.
    assert r["groups"]["on_time"] == 1 and r["groups"]["no_due_date"] == 6
