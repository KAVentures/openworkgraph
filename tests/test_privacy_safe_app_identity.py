"""Unrecognized apps remain separate workflow evidence, not one 'Other app' bucket."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gateway import manager_insights
from shared.core.normalizer import safe_surface


def test_browser_identity_uses_real_work_site_not_arbitrary_tab_title():
    assert safe_surface(app="Google Chrome", hostname="acme.zendesk.com") == "Zendesk"
    assert safe_surface(app="Chrome", hostname="store.myshopify.com") == "Shopify"
    assert safe_surface(app="Chrome", hostname="system.netsuite.com") == "NetSuite"
    assert safe_surface(app="Chrome", hostname="dashboard.stripe.com") == "Stripe"
    assert safe_surface(app="Chrome", hostname="app.fortnox.se") == "Fortnox"
    assert safe_surface(app="Chrome", hostname="evilzendesk.com") != "Zendesk"
    assert safe_surface(app="Chrome", hostname="stripe.com.evil.invalid") != "Stripe"


def test_manager_view_recognizes_business_domains_without_customer_subdomains():
    assert manager_insights._surface({"app": "Chrome", "metadata": {
        "page": {"hostname": "customer-a.zendesk.com"}}}) == "Zendesk"
    assert manager_insights._surface({"app": "Chrome", "page_host": "acme.myshopify.com"}) == "Shopify"
    assert manager_insights._surface({"app": "Chrome", "page_host": "system.netsuite.com"}) == "NetSuite"
    assert manager_insights._surface({"app": "Chrome", "page_host": "dashboard.stripe.com"}) == "Stripe"


def test_unknown_apps_and_sites_receive_distinct_report_scoped_aliases():
    aliases = {}
    private_app = {"app": "Confidential Customer Alpha App"}
    another_app = {"app": "Confidential Customer Beta App"}
    private_site = {"app": "Google Chrome", "metadata": {
        "page": {"hostname": "alpha.project-merger.internal"}}}
    another_site = {"app": "Google Chrome", "metadata": {
        "page": {"hostname": "beta.project-merger.internal"}}}
    first = manager_insights._surface(private_app, aliases=aliases)
    assert first == manager_insights._surface(private_app, aliases=aliases)
    assert first != manager_insights._surface(another_app, aliases=aliases)
    site_one = manager_insights._surface(private_site, aliases=aliases)
    site_two = manager_insights._surface(another_site, aliases=aliases)
    assert site_one != site_two and site_one.startswith("Work site ")
    assert first.startswith("Work app ")
    assert not any(token in " ".join(aliases.values()) for token in
                   ("Confidential", "Alpha", "Beta", "project-merger", "internal"))
    assert manager_insights._surface({"app": "Excluded"}, aliases=aliases) == "Other app"


def test_unknown_work_apps_contribute_to_aggregate_motifs_without_name_leaks(monkeypatch):
    start = datetime(2026, 10, 18, 8, tzinfo=timezone.utc)
    secret_apps = (
        "Private Customer Alpha Tool",
        "Private Customer Beta Tool",
        "Private Customer Gamma Tool",
        "Private Customer Alpha Tool",
    )
    sample = []
    for person in range(3):
        for index, app in enumerate(secret_apps):
            sample.append({
                "observed_at": (start + timedelta(hours=person, minutes=index)).isoformat(),
                "actor_id": f"employee-{person}",
                "device_id": f"device-{person}",
                "session_id": f"session-{person}",
                "app": app,
                "window_title": "SECRET_CUSTOMER_NAME",
                "duration_seconds": 60,
            })
    monkeypatch.setattr(manager_insights, "load_gateway_evidence", lambda *_args, **_kw: sample)
    report = manager_insights.manager_insights(None, organization_id="org-work")
    assert report["workflows"], report
    apps = report["workflows"][0]["apps"]
    assert len(apps) == 4 and len(set(apps)) == 3
    assert all(app.startswith("Work app ") for app in apps)
    assert apps[0] == apps[-1]
    assert report["workflows"][0]["contributing_people"] == 3
    for private in ("Private Customer", "SECRET_CUSTOMER_NAME", "Alpha", "Beta", "Gamma"):
        assert private not in str(report)


def test_unknown_work_sites_contribute_without_revealing_hostnames(monkeypatch):
    start = datetime(2026, 10, 18, 8, tzinfo=timezone.utc)
    hosts = (
        "support.customer-a.internal",
        "orders.customer-a.internal",
        "billing.customer-a.internal",
        "support.customer-a.internal",
    )
    sample = []
    for person in range(3):
        for index, host in enumerate(hosts):
            sample.append({
                "observed_at": (start + timedelta(hours=person, minutes=index)).isoformat(),
                "actor_id": f"employee-{person}",
                "device_id": f"device-{person}",
                "session_id": f"session-{person}",
                "app": "Google Chrome",
                "metadata": {"page": {"hostname": host}},
                "duration_seconds": 60,
            })
    monkeypatch.setattr(manager_insights, "load_gateway_evidence", lambda *_args, **_kw: sample)
    report = manager_insights.manager_insights(None, organization_id="org-work")
    assert report["workflows"], report
    apps = report["workflows"][0]["apps"]
    assert len(set(apps)) == 3 and all(name.startswith("Work site ") for name in apps)
    assert "customer-a.internal" not in str(report)


def test_repeated_workflow_projection_preserves_unknown_surface_distinctions(monkeypatch):
    from server import workflow_evidence
    from server.procedural_memory import _surface_key
    from server import procedural_feedback, agent_execution_traces

    monkeypatch.setattr(procedural_feedback, "_human_runs", lambda *_a, **_kw: [])
    monkeypatch.setattr(agent_execution_traces, "agent_execution_traces",
                        lambda *_a, **_kw: {"executions": []})
    unknown_one = "Client Alpha Confidential Tool"
    unknown_two = "Client Beta Confidential Tool"
    execution = {
        "execution_id": "execution:1111111111111111",
        "actor_kind": "human",
        "steps": [_surface_key(unknown_one), _surface_key("Zendesk"),
                  _surface_key(unknown_two)],
    }
    raw = [
        {"event_type": "focus_span", "app": unknown_one},
        {"event_type": "browser_click", "app": "Google Chrome",
         "metadata": {"page": {"hostname": "customer.zendesk.com"}}},
        {"event_type": "focus_span", "app": unknown_two},
    ]
    projection = workflow_evidence._candidate_runs(raw, [execution])[0]["surface_labels"]
    assert projection[_surface_key("Zendesk")] == "Zendesk"
    first = projection[_surface_key(unknown_one)]
    second = projection[_surface_key(unknown_two)]
    assert first.startswith("Work surface ")
    assert second.startswith("Work surface ")
    assert first != second
    assert unknown_one not in str(projection) and unknown_two not in str(projection)
    assert "Other surface (redacted)" not in str(projection)
