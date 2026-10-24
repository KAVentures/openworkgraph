"""Team-level task discovery from k-anonymous synced aggregates (roadmap #326 A4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from gateway.auth import Principal
from test_gateway_human_access_v56 import FakeVerifier, _claims, _headers, _human_settings, _settings
from gateway.hardening import HardeningSettings
from gateway.human_enterprise_app import create_human_enterprise_app

ADMIN = _headers("admin-secret")
WINDOW = {"since": "2026-09-07T00:00:00Z", "until": "2026-09-28T00:00:00Z"}  # three complete ISO weeks
INVOICE = ("Gmail", "Microsoft Excel", "Fortnox", "Gmail")
TICKET = ("Zendesk", "Slack", "Jira")


def _app(tmp_path):
    verifier = FakeVerifier({
        "manager": _claims("manager", ["eng-managers"]),
        "aggregate": _claims("analyst", ["aggregate-analysts"]),
        "aggregate-manager": _claims("lead", ["aggregate-analysts", "eng-managers"]),
        "aggregate-org": _claims("cfo", ["aggregate-analysts", "org-auditors"]),
    })
    return create_human_enterprise_app(settings=_settings(tmp_path), hardening=HardeningSettings(),
                                       human_access=_human_settings(), verifier=verifier)


def _work(app, actor, apps, *, day=datetime(2026, 9, 15, tzinfo=timezone.utc), private="PRIVATE_CUSTOMER_9"):
    principal = Principal(token_id=f"seed-{actor}", token_type="device", organization_id="acme",
                          actor_id=actor, device_id=f"device-{actor}", scopes=frozenset({"evidence:write"}))
    events = []
    for run in range(2):
        for step, name in enumerate(apps):
            events.append({
                "event_id": f"{actor}-{day:%m%d}-{run}-{step}-{'-'.join(apps)}",
                "observed_at": (day + timedelta(hours=9 + run, minutes=step)).isoformat(),
                "event_type": "app_focus", "app": name, "window_title": private,
                "session_id": f"session-{actor}-{day:%m%d}-{run}", "duration_seconds": 120,
                "metadata": {"message_body": "LEAK"},
            })
    app.state.db.insert_events(principal, events)


def _assign(client, app, actor, teams, *, at="2026-08-01T00:00:00.000000Z"):
    response = client.put(f"/v1/admin/access/acme/actors/{actor}/teams", headers=ADMIN, json={"teams": teams})
    assert response.status_code == 200, response.text
    with app.state.db.connect() as conn:
        app.state.db._execute(conn, "UPDATE gateway_actor_teams SET updated_at = ? WHERE actor_id = ?", (at, actor))


def _seeded(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    client.__enter__()
    for index in range(4):
        _assign(client, app, f"eng-{index}", ["engineering"])
        _work(client.app, f"eng-{index}", INVOICE)
    for index in range(3):
        _assign(client, app, f"fin-{index}", ["finance"])
        _work(app, f"fin-{index}", INVOICE)
        _work(app, f"fin-{index}", TICKET, day=datetime(2026, 9, 16, tzinfo=timezone.utc))
    for index in range(2):
        _assign(client, app, f"ops-{index}", ["ops"])
        _work(app, f"ops-{index}", INVOICE)
    _work(app, "eng-0", TICKET, day=datetime(2026, 9, 16, tzinfo=timezone.utc))   # only one engineer
    return app, client


def test_admin_sees_team_patterns_only_above_the_floor(tmp_path):
    app, client = _seeded(tmp_path)
    report = client.get("/v1/admin/team-insights/acme", headers=ADMIN, params=WINDOW).json()
    teams = {team["team_id"]: team for team in report["teams"]}
    assert set(teams) == {"engineering", "finance"} and report["suppressed_teams"] == ["ops"]
    assert [p["apps"] for p in teams["engineering"]["patterns"]] == [["Gmail", "Excel", "Fortnox", "Gmail"]]
    assert sorted(p["apps"][0] for p in teams["finance"]["patterns"]) == ["Gmail", "Zendesk"]
    invoice = teams["engineering"]["patterns"][0]
    assert invoice["contributing_people"] == 3 and invoice["also_in_other_teams"] == 1  # 4 is reported as the floor
    assert report["cross_team_patterns"] == [{"apps": ["Gmail", "Excel", "Fortnox", "Gmail"],
                                             "teams": ["engineering", "finance"], "team_count": 2,
                                             "each_team_meets_minimum_independently": True}]
    text = str(report)
    for leak in ("PRIVATE_CUSTOMER", "LEAK", "eng-0", "fin-1", "ops-0", "session-", "device-"):
        assert leak not in text
    assert report["effective_since"].startswith("2026-09-07") and report["effective_until"].startswith("2026-09-27")
    one = client.get("/v1/admin/team-insights/acme", headers=ADMIN, params={**WINDOW, "team": "ops"}).json()
    assert one["teams"] == [] and one["suppressed_teams"] == ["ops"]
    assert client.get("/v1/admin/team-insights/acme", params=WINDOW).status_code in (401, 403)
    client.__exit__(None, None, None)


def test_work_before_a_team_assignment_never_counts(tmp_path):
    app, client = _seeded(tmp_path)
    # Re-assigning engineers after their September work: that work no longer counts for the team.
    for index in range(4):
        _assign(client, app, f"eng-{index}", ["engineering"], at="2026-09-20T00:00:00.000000Z")
    report = client.get("/v1/admin/team-insights/acme", headers=ADMIN, params=WINDOW).json()
    teams = {team["team_id"]: team for team in report["teams"]}
    assert teams["engineering"]["patterns"] == [] and report["cross_team_patterns"] == []
    # Assignments made after the window ends do not count at all.
    for index in range(3):
        _assign(client, app, f"fin-{index}", ["finance"], at="2026-10-01T00:00:00.000000Z")
    report = client.get("/v1/admin/team-insights/acme", headers=ADMIN, params=WINDOW).json()
    assert "finance" in report["suppressed_teams"]
    client.__exit__(None, None, None)


def test_partial_weeks_are_never_exposed(tmp_path):
    app, client = _seeded(tmp_path)
    report = client.get("/v1/admin/team-insights/acme", headers=ADMIN,
                        params={"since": "2026-09-15T00:00:00Z", "until": "2026-09-16T23:00:00Z"}).json()
    assert report["teams"] == [] and report["suppression_reason"] == "no_complete_utc_week_in_requested_window"
    client.__exit__(None, None, None)


def test_human_routes_need_aggregate_scope_and_stay_inside_team_scopes(tmp_path):
    app, client = _seeded(tmp_path)
    path = "/v1/human/aggregate/team-patterns"
    assert client.get(path, headers=_headers("manager"), params=WINDOW).status_code == 403   # no aggregate:read
    assert client.get(path, headers=_headers("aggregate"), params=WINDOW).status_code == 403  # no team scope
    lead = client.get(path, headers=_headers("aggregate-manager"), params=WINDOW).json()
    assert [team["team_id"] for team in lead["teams"]] == ["engineering"]
    assert lead["cross_team_patterns"] == []  # other teams are outside the caller's scope
    org = client.get(path, headers=_headers("aggregate-org"), params=WINDOW).json()
    assert {team["team_id"] for team in org["teams"]} == {"engineering", "finance"}
    with app.state.db.connect() as conn:
        actions = [row[0] for row in app.state.db._execute(conn, "SELECT action FROM audit_log").fetchall()]
    assert actions.count("human_access.team_aggregate.read") == 2
    assert "manager.team_insights.read" not in actions
    client.__exit__(None, None, None)


def test_default_window_is_recent_complete_weeks(tmp_path):
    app, client = _seeded(tmp_path)
    report = client.get("/v1/admin/team-insights/acme", headers=ADMIN).json()
    since = datetime.fromisoformat(report["effective_since"].replace("Z", "+00:00"))
    until = datetime.fromisoformat(report["effective_until"].replace("Z", "+00:00"))
    assert since.weekday() == 0 and since.hour == 0 and (until - since).days in (20, 27)
    assert until < datetime.now(timezone.utc)
    client.__exit__(None, None, None)


def test_admin_route_works_without_oidc_human_access(tmp_path):
    from gateway.enterprise_app import create_enterprise_app
    with TestClient(create_enterprise_app(settings=_settings(tmp_path), hardening=HardeningSettings())) as client:
        report = client.get("/v1/admin/team-insights/acme", headers=ADMIN, params=WINDOW)
        assert report.status_code == 200, report.text
        assert report.json()["teams"] == [] and report.json()["suppressed_teams"] == []
