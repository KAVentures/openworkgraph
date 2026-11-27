"""Synthetic Workday RaaS integration and retained-feed acceptance."""
import base64
from datetime import timedelta
import json

import httpx
import pytest

from server import workday_raas_feed as wd
from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import event_log_workspace as ws
from shared.enterprise_connector_contract import capabilities

ENV = "OWG_WORKDAY_PASSWORD_TEST"
PASSWORD = "fixture-secret-password"
HOST = "wd2-impl-services1.workday.com"
BASE = wd.BASE + "fixture/owner/"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "DATA_DIR", tmp_path)
    monkeypatch.setenv(ENV, PASSWORD)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES", raising=False)
    src = {"host": HOST, "tenant": "fixture", "report_owner": "owner",
           "event_report": "OWG_Events", "count_report": "OWG_Control_Count",
           "integration_user": "owg_isu", "credential_env": ENV,
           "since_prompt": "Since", "until_prompt": "Until",
           "event_field": "Event_ID", "case_field": "Case_ID",
           "activity_field": "Activity", "timestamp_field": "Event_At",
           "resource_field": "Resource_ID", "count_field": "Total",
           "activity_labels": {"open": "Open", "close": "Close"}}
    monkeypatch.setenv(wd.CATALOG, json.dumps({"primary": src}))
    return src


def config(**extra):
    since = (rest._now() - timedelta(days=3)).replace(microsecond=0)
    return {"name": "Workday test", "source_alias": "primary",
            "source_sha256": wd.catalog_public()["sources"][0]["source_sha256"],
            "initial_since": rest._iso(since), "window_hours": 24,
            "overlap_minutes": 60, "ingestion_lag_minutes": 15,
            "confirm_configuration": True, "confirm_source_read": True,
            "confirm_metadata_retention": True, **extra}


def make_rows(profile):
    start = rest._stamp(profile["config"]["initial_since"])
    at = start + timedelta(hours=23, minutes=30)
    return [{"Event_ID": "private-event-" + str(i), "Case_ID": "private-case",
             "Activity": label, "Event_At": rest._iso(at + timedelta(seconds=i)),
             "Resource_ID": "private-resource"}
            for i, label in enumerate(("open", "close"))]


def run(profile, rows, *, change=None):
    seen = []
    def handler(req):
        assert req.method == "GET" and req.url.host == HOST
        assert req.headers["authorization"] == "Basic " + base64.b64encode(
            ("owg_isu@fixture:" + PASSWORD).encode()).decode()
        assert set(req.url.params.keys()) == {"format", "Since", "Until"}
        assert req.url.params["format"] == "json"
        assert rest._stamp(req.url.params["Since"]) < rest._stamp(req.url.params["Until"])
        seen.append(req.url.path)
        if req.url.path == BASE + "OWG_Control_Count":
            data = [{"Total": str(len(rows))}]
        else:
            assert req.url.path == BASE + "OWG_Events"
            data = [dict(row) for row in rows]
            # Equivalent results may arrive in different order.
            if len(seen) == 3:
                data.reverse()
        response = {"Report_Entry": data}
        if change:
            overridden = change(response, len(seen))
            if isinstance(overridden, httpx.Response):
                return overridden
        return httpx.Response(200, json=response)
    def factory(**kwargs):
        assert not kwargs["trust_env"] and not kwargs["follow_redirects"]
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    return rest.run(profile["profile_id"], profile["revision"],
                    confirm_reads=True, confirm_retention=True, client_factory=factory), seen


def test_native_report_count_repeat_replay_and_privacy():
    p = wd.create(config())
    rows = make_rows(p)
    first, paths = run(p, rows)
    assert first["events_added"] == 2
    assert paths == [BASE + report for report in
                     ("OWG_Control_Count", "OWG_Events", "OWG_Events", "OWG_Control_Count")]
    assert first["last_result"]["control_count_agreement"]
    assert first["last_result"]["repeat_scan_agreement"]
    assert not first["last_result"]["provider_completeness_verified"]
    assert not first["last_result"]["transaction_snapshot_verified"]
    second, _ = run(first, rows)
    assert second["events_added"] == 0 and second["duplicates_skipped"] == 2
    assert ws.list_logs()["logs"][0]["events"] == 2
    with ws._LOCK:
        c = rest._connect()
        try:
            raw = str(c.execute("SELECT * FROM events").fetchall()) + str(c.execute("SELECT * FROM objects").fetchall())
            stored = str(rest._get(c, p["profile_id"]))
        finally:
            c.close()
    assert all(x not in raw + stored for x in (PASSWORD, "private-event", "private-case", "private-resource"))
    assert rest.schedule_pin(p["profile_id"]) == p["revision"]
    assert capabilities("workday")["exact_snapshot_publication"]
    assert any(x["provider"] == "workday" for x in ops.inventory()["retained_adapter_capabilities"])


def test_empty_window_requires_control_agreement():
    p = wd.create(config())
    report, paths = run(p, [])
    assert len(paths) == 4 and report["events_added"] == 0 and report["checkpoint"]
    assert report["log_id"] is None


@pytest.mark.parametrize("kind", ["count_low", "count_high", "count_drift",
                                  "count_missing", "count_fraction", "unknown_root",
                                  "missing_row", "drift", "duplicate", "extra_field",
                                  "nested_identity", "unapproved_activity", "outside",
                                  "naive_time", "precision", "redirect", "html", "rate_limit"])
def test_failed_window_leaves_no_retention(kind):
    p = wd.create(config())
    rows = make_rows(p)
    if kind == "duplicate":
        rows[1]["Event_ID"] = rows[0]["Event_ID"]
    elif kind == "extra_field":
        rows[0]["Salary"] = "private"
    elif kind == "nested_identity":
        rows[0]["Case_ID"] = {"name": "private"}
    elif kind == "unapproved_activity":
        rows[0]["Activity"] = "unreviewed"
    elif kind == "outside":
        rows[0]["Event_At"] = "2000-01-01T00:00:00Z"
    elif kind == "naive_time":
        rows[0]["Event_At"] = rows[0]["Event_At"].rstrip("Z")
    elif kind == "precision":
        rows[0]["Event_At"] = rows[0]["Event_At"].rstrip("Z") + ".1234567Z"
    def change(payload, step):
        data = payload["Report_Entry"]
        if kind == "count_low" and step == 1:
            data[0]["Total"] = "1"
        elif kind == "count_high" and step == 1:
            data[0]["Total"] = "10001"
        elif kind == "count_drift" and step == 4:
            data[0]["Total"] = "3"
        elif kind == "count_missing" and step == 1:
            data.clear()
        elif kind == "count_fraction" and step == 1:
            data[0]["Total"] = "2.0"
        elif kind == "unknown_root" and step == 2:
            payload["next"] = "https://untrusted.invalid"
        elif kind == "missing_row" and step == 2:
            data.pop()
        elif kind == "drift" and step == 3:
            data[0]["Case_ID"] = "changed"
        elif kind == "redirect" and step == 1:
            return httpx.Response(302, headers={"Location": "https://untrusted.invalid"})
        elif kind == "html" and step == 1:
            return httpx.Response(200, text="<html>Login</html>", headers={"Content-Type": "text/html"})
        elif kind == "rate_limit" and step == 1:
            return httpx.Response(429, headers={"Retry-After": "120"})
    with pytest.raises(rest.FeedError):
        run(p, rows, change=change)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert ws.list_logs()["logs"] == []


def test_passive_configuration_and_binding(monkeypatch, setup):
    assert wd.catalog_public()["provider_reads_performed"] is False
    with pytest.raises(rest.FeedError):
        wd.create(config(confirm_source_read=False))
    p = wd.create(config())
    monkeypatch.setenv(ENV, "different-password")
    with pytest.raises(rest.FeedError, match="credential_changed"):
        rest.schedule_pin(p["profile_id"])
    monkeypatch.setenv(ENV, PASSWORD)
    setup["count_report"] = "Different_Control"
    monkeypatch.setenv(wd.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="definition_changed"):
        rest.schedule_pin(p["profile_id"])


@pytest.mark.parametrize(("field", "value"), [
    ("host", "wd2-impl-services1.workday.com.evil.invalid"),
    ("host", "https://wd2-impl-services1.workday.com"),
    ("tenant", "../other"),
    ("report_owner", "owner/other"),
    ("event_report", "OWG_Control_Count"),
    ("until_prompt", "Since"),
    ("since_prompt", "format"),
    ("case_field", "Event_ID"),
    ("activity_labels", {"open": "Same", "close": "Same"}),
])
def test_bad_deployment_catalog(field, value, setup, monkeypatch):
    setup[field] = value
    monkeypatch.setenv(wd.CATALOG, json.dumps({"primary": setup}))
    with pytest.raises(rest.FeedError, match="invalid_workday_deployment_catalog"):
        wd.catalog()
