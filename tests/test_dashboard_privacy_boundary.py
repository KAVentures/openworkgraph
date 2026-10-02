from __future__ import annotations

import json
import re
from pathlib import Path


def _insert_context_row(event_id, *, source, surface, action, title, locator, label, text, event_type):
    from server import db

    with db.connect() as conn:
        conn.execute("DELETE FROM context_events WHERE event_id = ?", (event_id,))
        conn.execute(
            """
            INSERT INTO context_events(
              event_id, observed_at, session_id, source, surface, action,
              resource_title, resource_locator, target_label, context_text, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, "2099-01-01T12:00:00+00:00", "dashboard-privacy-session", source, surface, action,
             title, locator, label, text, json.dumps({"event_type": event_type})),
        )


def test_paged_dashboard_evidence_keeps_title_context_with_sensitive_details_tokenized():
    from server import db
    from server.evidence_query import query_evidence

    db.init_db()
    event_id = "dashboard-private-canary-evidence"
    subject = "Contract renewal SUBJECT_CANARY_91827"
    private_name = "Anna Svensson"
    private_email = "anna.svensson@example.com"
    private_path = "mail.google.com/mail/u/0/#inbox/private-anna-case"
    # A row stored before titles were protected at rest: the display pass
    # must still tokenize the name and address.
    _insert_context_row(
        event_id, source="browser_extension", surface="Gmail", action="click",
        title=f"{subject} - {private_name} <{private_email}> - Gmail", locator=private_path,
        label=f"Send to {private_name}", text=f"Gmail | click | {private_name} | {subject} | {private_path}",
        event_type="browser_click",
    )

    result = query_evidence(scope="all", since=None, limit=20, q="SUBJECT_CANARY_91827")
    item = next(x for x in result["items"] if x["event_id"] == event_id)
    blob = json.dumps(item, ensure_ascii=False)

    # Context is kept (the subject and the tool), sensitive details are tokens.
    assert result["total"] >= 1
    assert subject in item["page"]
    assert item["page"] == item["resource_title"]
    assert re.search(r"PERSON_[0-9A-F]{6}", item["page"])
    assert private_name not in blob
    assert private_email not in blob
    # URL paths and raw UI labels never cross the dashboard boundary.
    assert private_path not in blob
    assert "inbox" not in blob
    assert item["surface"] == "Gmail"
    assert item["action"] == "Send"
    assert result["presentation_layer"] == "dashboard_sensitive_details_tokenized"


def test_paged_dashboard_evidence_is_human_work_only_by_default():
    from server import db
    from server.evidence_query import query_evidence

    db.init_db()
    _insert_context_row(
        "dashboard-agent-row", source="agent", surface="Claude Code", action="tool_call",
        title="", locator="", label="", text="Claude Code | tool_call | AGENT_ROW_CANARY_5521",
        event_type="agent_tool_call",
    )
    human = query_evidence(scope="all", since=None, limit=50, q="AGENT_ROW_CANARY_5521")
    assert human["total"] == 0 and human["items"] == []
    assert all(f["surface"] != "Claude Code" for f in query_evidence(scope="all", since=None, limit=1)["surfaces"])
    with_agents = query_evidence(scope="all", since=None, limit=50, q="AGENT_ROW_CANARY_5521", include_agents=True)
    assert [x["event_id"] for x in with_agents["items"]] == ["dashboard-agent-row"]


def test_dashboard_title_display_fails_closed():
    from server import evidence_query

    import browser_title_privacy

    original = browser_title_privacy.protect_text
    browser_title_privacy.protect_text = lambda value: (_ for _ in ()).throw(RuntimeError("detector down"))
    try:
        item = evidence_query._row_item({"event_id": "x", "surface": "Gmail", "resource_title": "Anna Svensson - Gmail",
                                         "metadata_json": "{}"})
    finally:
        browser_title_privacy.protect_text = original
    assert item["resource_title"] == "" and item["page"] == "Gmail"


def test_dashboard_title_redacts_before_display_truncation():
    from server import evidence_query

    import browser_title_privacy

    title = ("Q" * 292) + " anna.svensson@example.com - Gmail"
    seen = []
    original = browser_title_privacy.protect_text
    browser_title_privacy.protect_text = lambda value: seen.append(value) or value.replace(
        "anna.svensson@example.com", "EMAIL_TOKEN"
    )
    try:
        displayed = evidence_query._display_title(title, {})
    finally:
        browser_title_privacy.protect_text = original

    # The privacy pass must see the complete value. Truncating first can cut an
    # identifier into an unrecognisable fragment and then expose that fragment.
    assert seen == [title]
    assert len(displayed) <= evidence_query._MAX_TITLE
    assert "anna.svensson@" not in displayed


def test_dashboard_title_oversize_legacy_value_fails_closed():
    from server import evidence_query

    import browser_title_privacy

    called = False
    original = browser_title_privacy.protect_text

    def protect(value):
        nonlocal called
        called = True
        return value

    browser_title_privacy.protect_text = protect
    try:
        displayed = evidence_query._display_title("X" * (evidence_query._MAX_TITLE_INPUT + 1), {})
    finally:
        browser_title_privacy.protect_text = original

    assert displayed == ""
    assert called is False


def test_evidence_cursor_fingerprint_includes_agent_scope():
    from server.evidence_query import _fingerprint

    human = _fingerprint("all", "", "", None, include_agents=False)
    mixed = _fingerprint("all", "", "", None, include_agents=True)
    assert human != mixed


def test_shared_dashboard_action_rule_keeps_semantics_without_private_text():
    from server.dashboard_privacy_policy import safe_dashboard_action

    private_name = "Anna Svensson"
    private_email = "anna@example.com"
    private_id = "CUSTOMER-739201"

    values = [
        safe_dashboard_action(f"Open email from {private_name}", event_type="browser_click"),
        safe_dashboard_action(f"Send to {private_email}", event_type="browser_click"),
        safe_dashboard_action(f"Open account {private_id}", event_type="browser_click"),
        safe_dashboard_action(f"Update status for {private_id}", event_type="browser_click"),
    ]
    assert values == ["Open email", "Send", "Open account", "Update status"]
    assert safe_dashboard_action(f"{private_name} {private_id}", event_type="browser_click") == "Click"
    blob = json.dumps(values, ensure_ascii=False)
    assert private_name not in blob
    assert private_email not in blob
    assert private_id not in blob


def test_dashboard_pattern_projection_drops_names_email_and_ids_but_keeps_workflow_actions():
    from server.dashboard_privacy_policy import dashboard_safe_patterns

    private_name = "Anna Svensson"
    private_email = "anna@example.com"
    private_id = "CUSTOMER-739201"
    payload = {
        "scope": "all",
        "patterns": [
            {
                "signature": f"customer.followup:{private_id}:{private_email}",
                "task_family": f"followup:{private_name}",
                "suggested_label": f"Follow up with {private_name}",
                "name": f"Follow up with {private_name}",
                "surfaces": ["Gmail", "Salesforce", "Google Sheets", "Gmail"],
                "action_skeleton": [
                    f"Open email from {private_name}",
                    f"Open account {private_id}",
                    f"Update status for {private_id}",
                    f"Send to {private_email}",
                ],
                "steps": [
                    {"surface": "Gmail", "action": f"Open email from {private_name}"},
                    {"surface": "Salesforce", "action": f"Open account {private_id}"},
                    {"surface": "Google Sheets", "action": f"Update status for {private_id}"},
                    {"surface": "Gmail", "action": f"Send to {private_email}"},
                ],
                "ends_with": f"Send to {private_email}",
                "runs": [
                    {
                        "semantic_actions": [
                            f"Open email from {private_name}",
                            f"Open account {private_id}",
                            f"Update status for {private_id}",
                            f"Send to {private_email}",
                        ],
                        "steps": [
                            {"surface": "Gmail", "action": f"Open email from {private_name}"},
                            {"surface": "Salesforce", "action": f"Open account {private_id}"},
                            {"surface": "Google Sheets", "action": f"Update status for {private_id}"},
                            {"surface": "Gmail", "action": f"Send to {private_email}"},
                        ],
                        "outcomes": [f"Send to {private_email}"],
                        "ends_with": f"Send to {private_email}",
                    }
                ],
                "observed_count": 2,
            }
        ],
    }

    safe = dashboard_safe_patterns(payload)
    pattern = safe["patterns"][0]
    blob = json.dumps(safe, ensure_ascii=False)
    assert private_name not in blob
    assert private_email not in blob
    assert private_id not in blob
    assert pattern["action_skeleton"] == ["Open email", "Open account", "Update status", "Send"]
    assert [step["action"] for step in pattern["steps"]] == [
        "Open email", "Open account", "Update status", "Send"
    ]
    assert pattern["ends_with"] == "Send"
    assert pattern["signature"].startswith("dashboard-")
    assert pattern["task_family"] == pattern["signature"]
    assert pattern["dashboard_data_layer"] == "content_minimized"


def test_operational_summary_and_live_status_minimizers_drop_dashboard_canaries():
    from server import analytics, db
    from server.dashboard_privacy_policy import safe_browser_status, safe_collector_status

    db.init_db()
    event_id = "dashboard-private-canary-summary"
    private_subject = "PRIVATE_SUMMARY_CANARY_44119"
    private_name = "Erik Nilsson"
    private_path = "/mail/u/0/#inbox/private-case"

    raw = {
        "event_id": event_id,
        "observed_at": "2099-01-01T12:01:00+00:00",
        "device_id": "dashboard-device",
        "session_id": "dashboard-privacy-session",
        "app": "Google Chrome",
        "window_title": f"{private_name} — {private_subject} - Gmail",
        "event_type": "browser_click",
        "duration_seconds": 0,
        "screenshot_path": None,
        "metadata": {
            "source": "browser_extension",
            "action": "click",
            "page": {
                "hostname": "mail.google.com",
                "pathname": private_path,
                "title": f"{private_name} — {private_subject} - Gmail",
            },
            "target": {"tag": "button", "label": f"Send to {private_name}"},
        },
    }
    db.insert_events([raw])

    result = analytics.summary(limit=10000, since=None, operational=True)
    result["collector"] = safe_collector_status(
        {
            "received_at": "2099-01-01T12:01:01+00:00",
            "window_title": f"{private_name} — {private_subject}",
            "app": "Google Chrome",
        }
    )
    result["browser_sensor"] = safe_browser_status(
        {
            "status": "connected",
            "received_at": "2099-01-01T12:01:01+00:00",
            "sensor_version": "test",
            "expected_sensor_version": "test",
            "version_ok": True,
            "hostname": "mail.google.com",
            "pathname": private_path,
            "page_title": f"{private_name} — {private_subject}",
        }
    )
    blob = json.dumps(result, ensure_ascii=False)

    assert private_subject not in blob
    assert private_name not in blob
    assert private_path not in blob
    assert result["data_layer"] == "operational_normalized"
    assert set(result["collector"].keys()) <= {"connected", "received_at"}
    assert "hostname" not in result["browser_sensor"]
    assert "pathname" not in result["browser_sensor"]
    assert "page_title" not in result["browser_sensor"]

    # Guard the endpoint wiring without importing a route/middleware module into
    # an already-started FastAPI app during the test suite.
    source = (Path(__file__).resolve().parents[1] / "server" / "dashboard_privacy.py").read_text(encoding="utf-8")
    assert "dashboard_safe_summary(summary(limit=limit, since=since, operational=True))" in source
    assert "safe_collector_status(COLLECTOR_STATUS)" in source
    assert "safe_browser_status(BROWSER_STATUS)" in source
    assert '@app.get("/v1/dashboard-patterns")' in source
    assert "dashboard_safe_patterns(corrected_patterns(scope=scope))" in source


def test_work_profile_dashboard_response_drops_rich_context_echoes():
    from server.dashboard_privacy_policy import dashboard_safe_profile

    private_name = "PRIVATE_WORK_PROFILE_NAME_55231"
    private_path = f"example.test/customer/{private_name}"
    profile = {
        "navigation_hunting_candidates": [
            {
                "surface": "CRM",
                "resource_locator": private_path,
                "visit_count": 4,
                "needs_review": True,
            }
        ],
        "rapid_click_candidates": [
            {
                "surface": "CRM",
                "resource_locator": private_path,
                "target_label": private_name,
                "max_clicks_in_1_5s": 4,
                "needs_review": True,
            }
        ],
    }

    safe = dashboard_safe_profile(profile)
    blob = json.dumps(safe, ensure_ascii=False)
    assert private_name not in blob
    assert private_path not in blob
    assert safe["navigation_hunting_candidates"][0]["surface"] == "CRM"
    assert safe["navigation_hunting_candidates"][0]["visit_count"] == 4
    assert safe["rapid_click_candidates"][0]["max_clicks_in_1_5s"] == 4
    assert safe["dashboard_data_layer"] == "content_minimized"


def test_dashboard_routes_preserve_mcp_profile_and_use_minimized_browser_profile():
    root = Path(__file__).resolve().parents[1]
    routes = (root / "server" / "work_profile_routes.py").read_text(encoding="utf-8")
    dashboard_js = (root / "dashboard" / "work_profile.js").read_text(encoding="utf-8")

    assert '@app.get("/v1/work-profile")' in routes
    assert "return redact_for_display(compute_work_profile(scope=scope))" in routes
    assert '@app.get("/v1/dashboard-work-profile")' in routes
    assert "dashboard_safe_profile(compute_work_profile(scope=scope))" in routes
    assert "/v1/dashboard-work-profile?scope=current" in dashboard_js
    assert "fetch('/v1/work-profile?scope=current" not in dashboard_js


def test_rich_patterns_endpoint_remains_available_for_authorized_local_context():
    root = Path(__file__).resolve().parents[1]
    rich = (root / "server" / "v0571_polish.py").read_text(encoding="utf-8")
    safe = (root / "server" / "dashboard_privacy.py").read_text(encoding="utf-8")

    assert '_replace_route("/v1/patterns", "GET", corrected_patterns)' in rich
    assert '@app.get("/v1/dashboard-patterns")' in safe
    assert "dashboard_safe_patterns(corrected_patterns(scope=scope))" in safe


def test_enterprise_runner_installs_dashboard_privacy_last():
    root = Path(__file__).resolve().parents[1]
    runner = (root / "server" / "enterprise_runner.py").read_text(encoding="utf-8")
    privacy = runner.index("import server.dashboard_privacy")
    assert privacy > runner.index("import server.evidence_paging")
    assert privacy > runner.index("import server.work_profile_routes")


def test_formatted_interaction_labels_keep_safe_verbs_without_names():
    from server.dashboard_privacy_policy import safe_dashboard_action

    assert safe_dashboard_action("click: Send (button)") == "Send"
    assert safe_dashboard_action("click: Open account (button)") == "Open account"
    leaked = safe_dashboard_action("click: Open email from Anna Svensson (button)")
    assert "Anna" not in leaked and leaked == "Open email"


def test_dashboard_fetches_privacy_safe_patterns_endpoint():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    dashboard_js = "".join(p.read_text(encoding="utf-8") for p in (root / "dashboard").glob("*.js"))
    dashboard_js += (root / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "/v1/dashboard-patterns" in dashboard_js
    assert "'/v1/patterns" not in dashboard_js and '"/v1/patterns' not in dashboard_js
