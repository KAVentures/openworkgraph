"""R9: saved-log case metrics must be scoped, bounded and aggregate-only."""
import pytest

from server import event_log_cohort_metrics as target
from server import event_log_workspace as workspace


DEFINITION = {"version": 1, "expression": {"field": "duration_seconds"}}


class FakeConnection:
    def __init__(self, n=2):
        self.n = n
        self.restricted = False
        self.aggregate_called = False

    def execute(self, query, params=None):
        if "count(DISTINCT case_id)" in query:
            return self
        if "SELECT epoch(max(ts) - min(ts))" in query:
            self.aggregate_called = True
            return self
        raise AssertionError("unexpected SQL: " + query)

    def fetchone(self):
        return (self.n,)

    def fetchall(self):
        return [(10, 2, 1), (20, 3, 2)]


def install(monkeypatch, connection):
    def fake_query(log_id, case_type, callback):
        assert log_id == "log_0123456789abcdef"
        return callback(connection)
    monkeypatch.setattr(workspace, "_query", fake_query)
    def fake_restrict(conn, chosen, log_id):
        assert conn is connection
        assert log_id == "log_0123456789abcdef"
        conn.restricted = True
    monkeypatch.setattr(target.cohorts, "restrict", fake_restrict)


def test_aggregate_only_and_cohort_restriction(monkeypatch):
    conn = FakeConnection()
    install(monkeypatch, conn)
    result = target.summarize(
        "log_0123456789abcdef", {"definition": DEFINITION, "cohort": {}}
    )
    assert conn.restricted and conn.aggregate_called
    assert result["metric"]["sum"] == "30"
    assert result["metric"]["rows"] == 2
    assert result["complete_cohort"] is True
    assert result["raw_cases_returned"] is False
    assert "case_id" not in str(result)


def test_oversize_cohort_rejected_without_fetching_rows(monkeypatch):
    conn = FakeConnection(target.MAX_CASES + 1)
    install(monkeypatch, conn)
    with pytest.raises(workspace.EventLogWorkspaceError, match="narrow the cohort"):
        target.summarize(
            "log_0123456789abcdef", {"definition": DEFINITION, "cohort": {}}
        )
    assert not conn.aggregate_called


@pytest.mark.parametrize("spec", [
    {},
    {"definition": DEFINITION, "cohort": {}, "unexpected": True},
    {"definition": {"version": 1, "expression": {"field": "secret"}}, "cohort": {}},
    {"definition": DEFINITION, "cohort": {"unknown": "x"}},
])
def test_bad_request_rejected(monkeypatch, spec):
    conn = FakeConnection()
    install(monkeypatch, conn)
    with pytest.raises(workspace.EventLogWorkspaceError):
        target.summarize("log_0123456789abcdef", spec)
    assert not conn.aggregate_called


def test_metric_route_requires_session_before_reading_body(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import event_log_routes

    app = FastAPI()
    app.include_router(event_log_routes.router)
    client = TestClient(app)
    response = client.post(
        "/v1/event-logs/log_0123456789abcdef/cohort-metric",
        json={"definition": DEFINITION, "cohort": {}},
    )
    assert response.status_code == 403
    assert "session" in response.json()["detail"].lower()
    assert response.headers.get("cache-control") is None
