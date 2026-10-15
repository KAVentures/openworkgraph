"""Actual Gateway enrollment/upload and manager projection for browser-shaped events.

Avoid mocked Gateway loaders and fabricated app=Gmail events. Pass events
through the employee connector's policy and device redaction before HTTP
ingestion, then check the administrator's authenticated aggregation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from connector.cloud_privacy import project_event_for_upload
from connector.policy import prepare_event_for_gateway
from gateway_identity_helpers import (
    LEGACY_ENROLL, add_employee, bootstrap_owner, make_app, personal_invite, enroll,
)
from gateway import manager_insights as insights

POLICY = {
    "share_excluded": False,
    "share_metadata": True,
    "share_window_titles": False,
    "allowed_event_types": [],
}
SECRET = "synthetic-local-device-redaction-secret-" + "a" * 20


def _event(person: int, day: int, n: int, app: str, hostname: str = "") -> dict:
    start = datetime(2026, 10, 8, 8 + person, tzinfo=timezone.utc)
    at = start + timedelta(days=day, minutes=n)
    meta = {"action": "navigate" if hostname else "focus"}
    if hostname:
        meta["page"] = {
            "hostname": hostname, "pathname": "/private-quote-123",
            "title": "PRIVATE_PATIENT_CUSTOMER_ACCOUNT",
        }
        meta["target"] = {"label": "PRIVATE_CUSTOMER_MSG_89"}
    event = {
        "event_id": f"v-browser-{person}-{day}-{n}",
        "observed_at": at.isoformat(), "schema_version": "1.0",
        "event_type": "browser_navigation" if hostname else "focus_span",
        "source": "browser_extension" if hostname else "desktop",
        "session_id": f"one-long-recording-session-{person}-{day}",
        "app": app, "window_title": "PRIVATE_CUSTOMER_IDENTIFIER",
        "duration_seconds": 75,
        "metadata": meta,
    }
    prepared = prepare_event_for_gateway(event, POLICY)
    assert prepared is not None
    public = project_event_for_upload(prepared, secret=SECRET)
    assert "PRIVATE_CUSTOMER" not in str(public)
    assert "private-quote" not in str(public)
    return public


def _day_events(person: int, day: int, *, with_slack: bool) -> list[dict]:
    # Chrome and browser_extension, not fabricated app='Gmail' or
    # app='Fortnox' as in the original 3-person CI fixture.
    items = [
        ("Google Chrome", "mail.google.com"),
        ("Google Chrome", "mail.google.com"),  # two sensors of same page
        ("Microsoft Excel", ""),
    ]
    if with_slack:
        items.append(("Google Chrome", "app.slack.com"))
    items.extend([
        ("Google Chrome", "app.fortnox.se"),
        ("Google Chrome", "mail.google.com"),
        ("Finder", ""),
        ("Google Chrome", "unknown-private.example"),
    ])
    return [_event(person, day, i, app, host) for i, (app, host) in enumerate(items)]


def test_real_connector_browser_sites_and_cross_day_work_are_top_rank(tmp_path):
    app, _db = make_app(tmp_path)
    with TestClient(app) as client:
        owner, _totp = bootstrap_owner(client)
        endpoint = "/v1/admin/manager-insights/acme"
        assert client.get(endpoint).status_code == 401
        for person in range(3):
            employee = add_employee(client, owner, f"worker{person}@acme.se")
            invite = personal_invite(client, owner, employee["employee_id"])
            joined = enroll(client, invite["token"], f"worker-device-{person}")
            assert joined.status_code == 200, joined.text
            token = joined.json()["token"]
            # One whole recording session per day. Slack is irrelevant chatter
            # between real invoicing steps on two employee's days.
            for day in range(2):
                payload = _day_events(person, day, with_slack=(day == 0 or person == 2))
                response = client.post(
                    "/v1/evidence/batch",
                    headers={"Authorization": "Bearer " + token},
                    json={"events": payload},
                )
                assert response.status_code == 200, response.text
                assert len(response.json()["acknowledged_event_ids"]) == len(payload)

            report = client.get(endpoint, headers=owner)
            assert report.status_code == 200, report.text
            if person < 2:
                assert report.json()["workflows"] == []
            else:
                result = report.json()
                assert result["workflows"], result
                top = result["workflows"][0]
                assert top["apps"] == ["Gmail", "Excel", "Fortnox", "Gmail"], result
                assert top["contributing_people"] == 3
                assert top["observed_sessions"] == 6
                assert top["automation_candidate"] is True
                assert result["privacy"]["suppresses_groups_below_threshold"]
                for forbidden in ("PRIVATE_", "worker-device-", "worker0@acme.se",
                                  "one-long-recording-session-", "private-quote"):
                    assert forbidden not in str(result)


def test_browser_site_matches_are_exact_domain_boundaries():
    assert insights._surface({"app": "Google Chrome", "metadata": {
        "page": {"hostname": "mail.google.com"}}}) == "Gmail"
    assert insights._surface({"app": "Browser", "page_host": "app.fortnox.se"}) == "Fortnox"
    assert insights._surface({"app": "Chrome", "metadata": {
        "page": {"hostname": "jira.acme.atlassian.net"}}}) == "Jira"
    assert insights._surface({"app": "Chrome", "metadata": {
        "page": {"hostname": "evilfortnox.se"}}}) not in {"Fortnox", "Gmail"}
    assert insights._surface({"app": "Chrome", "metadata": {
        "page": {"hostname": "mail.google.com.evil.example"}}}) != "Gmail"
    assert insights._surface({"app": "Google Chrome", "metadata": {
        "page": {"hostname": "secret-customer.internal"}}}) == "Browser"


def test_single_long_session_motifs_skip_only_incidental_steps():
    ts = datetime(2026, 10, 8, tzinfo=timezone.utc).timestamp()
    events = [
        (ts + 60 * i, name, 60.0) for i, name in enumerate(
            ("Gmail", "Excel", "Slack", "Fortnox", "Gmail",
             "Finder", "Browser"))
    ]
    motifs = insights._motifs(events)
    assert ("Gmail", "Excel", "Fortnox", "Gmail") in motifs
    assert ("Excel", "Slack", "Excel", "Slack") not in motifs
